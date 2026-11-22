package com.flagdizero.jenny

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.os.Build
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Lo schermo come lo vede chi deve usarlo: un elenco di **elementi numerati**
 * su cui si può agire, più il testo che c'è intorno.
 *
 * È il gemello di `res/raw/browser_agent.js` per le app native, con lo stesso
 * contratto verso il modello: una riga per elemento,
 * `- ruolo "nome" [ref=V:eN stato]`, e il testo non azionabile come righe
 * semplici. Il `ref` vale solo per l'ultima lettura ([version]): dopo
 * un'azione lo schermo è un altro e i numeri si rifanno.
 *
 * ## Cosa si legge
 *
 * Tutte le finestre interattive, non solo l'app: un dialogo dei permessi, la
 * tendina aperta, il blocco schermo sono finestre loro, e chi usa il telefono
 * li vede. Restano fuori le finestre di Jenny, la barra di stato (rumore) e i
 * tasti della tastiera (si scrive con `type`, non un tasto alla volta).
 *
 * ## Cosa non esce mai
 *
 * * il contenuto dei campi password: la riga c'è, il nome no;
 * * le app che vietano gli screenshot (banche, schede in incognito): stessa
 *   regola di [ScreenSnapshots], la finestra si dichiara e non si legge.
 *
 * ## Riduzione
 *
 * Un contenitore cliccabile senza nome prende il nome dai testi che contiene
 * («Rete e Internet · Wi‑Fi, hotspot») e quei testi non si ripetono sotto:
 * misurato su `jenny_s22` il 22/11/2026, Impostazioni scende da 35 righe a
 * una per voce. Oltre [maxChars] ci si ferma e lo si dice.
 */
object PhoneTree {

    /** Nodi visitati al massimo: ogni nodo è una chiamata fra processi. */
    private const val MAX_NODES = 6_000

    /** Attesa dello screenshot che dice se la finestra è sicura (~20 ms). */
    private const val SECURE_PROBE_MS = 1_500L

    private const val NAME_CHARS = 80
    private const val TEXT_CHARS = 400

    /** Un elemento su cui si può agire, con il nodo da cui è nato. */
    class Element(
        val ref: String,
        val role: String,
        val name: String,
        val password: Boolean,
        val node: AccessibilityNodeInfo,
    )

    /** Una lettura: il testo per il modello e l'indice dei riferimenti. */
    class Snapshot(
        val version: Int,
        val packageName: String,
        val app: String,
        val title: String?,
        val secure: Boolean,
        val text: String,
        val truncated: Boolean,
        val elements: Map<String, Element>,
    ) {
        /** Per Python: il testo va al modello, `index` resta al codice (serve
         *  alla politica dei tocchi: ruolo e nome di ogni ref). */
        fun json(): String = JSONObject().apply {
            put("version", version)
            put("package", packageName)
            put("app", app)
            title?.let { put("title", it) }
            put("secure", secure)
            put("text", text)
            put("truncated", truncated)
            put("refs", elements.size)
            put("index", JSONObject().apply {
                for (e in elements.values) {
                    put(e.ref, JSONArray().put(e.role).put(if (e.password) "" else e.name))
                }
            })
        }.toString()
    }

    private val lock = Any()
    private var counter = 0
    private var latest: Snapshot? = null

    /** L'ultima risposta alla domanda «questa app vieta gli screenshot?». */
    @Volatile
    private var lastSecure: Pair<String, Boolean>? = null
    private val callbacks = Executors.newSingleThreadExecutor { Thread(it, "jenny-phone-shot") }

    /** L'ultima lettura, da cui le azioni risolvono i ref. */
    fun current(): Snapshot? = synchronized(lock) { latest }

    /** Butta l'ultima lettura (servizio spento, fine della guida). */
    fun clear() = synchronized(lock) { latest = null }

    /**
     * Legge lo schermo adesso. *filter* tiene solo le righe che lo contengono
     * (senza distinguere maiuscole); *maxChars* è il tetto sul testo.
     */
    fun snapshot(svc: AccessibilityService, filter: String?, maxChars: Int): Snapshot {
        val own = svc.packageName
        val windows = svc.windows.filter { keep(it, own) }
        val main = windows.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_APPLICATION && it.isActive }
            ?: windows.firstOrNull { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
            ?: windows.firstOrNull()
        val pkg = main?.root?.packageName?.toString().orEmpty()
        val version = synchronized(lock) { ++counter }
        val out = Out(version, filter?.trim()?.lowercase()?.takeIf { it.isNotEmpty() }, maxChars)
        val secure = main != null && isSecure(svc, main, pkg)
        if (secure) {
            out.line("(this app hides its screen from screenshots, so it is not read and not touched)")
        } else {
            for (window in windows) {
                val root = window.root ?: continue
                if (window !== main) out.line(sectionTitle(window, root))
                if (window.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD) {
                    out.line("(the keyboard is open)")
                    continue
                }
                walk(root, out, 0)
            }
        }
        val snap = Snapshot(
            version = version,
            packageName = pkg,
            app = appLabel(svc, pkg),
            title = main?.title?.toString()?.takeIf { it.isNotBlank() },
            secure = secure,
            text = out.sb.toString().trimEnd(),
            truncated = out.truncated,
            elements = out.elements,
        )
        synchronized(lock) { latest = snap }
        return snap
    }

    /** Le finestre che contano per chi usa il telefono. */
    private fun keep(window: AccessibilityWindowInfo, own: String): Boolean {
        val root = window.root ?: return false
        if (root.packageName?.toString() == own) return false
        if (window.type == AccessibilityWindowInfo.TYPE_SYSTEM) {
            // La barra di stato e quella di navigazione sono strisce: tendina,
            // blocco schermo e dialoghi di sistema sono finestre grandi.
            val b = Rect().also { window.getBoundsInScreen(it) }
            return window.isActive || window.isFocused || b.height() > 400
        }
        return window.type == AccessibilityWindowInfo.TYPE_APPLICATION ||
            window.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD
    }

    private fun sectionTitle(window: AccessibilityWindowInfo, root: AccessibilityNodeInfo): String {
        val what = when (window.type) {
            AccessibilityWindowInfo.TYPE_SYSTEM -> "system"
            AccessibilityWindowInfo.TYPE_INPUT_METHOD -> "keyboard"
            else -> "window"
        }
        val title = window.title?.toString()?.takeIf { it.isNotBlank() } ?: root.packageName
        return "## $what: $title"
    }

    private class Out(val version: Int, val filter: String?, val maxChars: Int) {
        val sb = StringBuilder()
        val elements = LinkedHashMap<String, Element>()
        var nodes = 0
        var truncated = false

        val full: Boolean get() = truncated || nodes >= MAX_NODES

        /** Aggiunge una riga se passa il filtro e sta nel tetto. Vero se entrata. */
        fun line(s: String): Boolean {
            if (truncated) return false
            if (filter != null && !s.startsWith("#") && !s.lowercase().contains(filter)) return false
            if (sb.length + s.length + 1 > maxChars) {
                truncated = true
                sb.append("… snapshot truncated at ").append(maxChars).append(" characters.\n")
                return false
            }
            sb.append(s).append('\n')
            return true
        }
    }

    private fun walk(node: AccessibilityNodeInfo, out: Out, depth: Int) {
        if (out.full || depth > 120) return
        out.nodes++
        if (!node.isVisibleToUser) return
        if (actionable(node)) {
            val own = ownName(node)
            // Un contenitore senza nome si chiama come quello che contiene, e
            // quel contenuto non si ripete sotto — a meno che dentro ci sia
            // altro su cui agire (una riga con il suo interruttore).
            // Un elenco che scorre non è un tasto: non prende il nome dalle voci.
            val gathered = if (own.isEmpty() && node.isClickable) gather(node) else null
            val name = (own.ifEmpty { gathered?.joinToString(" · ").orEmpty() }).take(NAME_CHARS)
            val ref = "${out.version}:e${out.elements.size + 1}"
            val line = "- ${role(node)} \"${if (node.isPassword) "" else name}\" [ref=$ref${state(node)}]"
            if (out.line(line)) {
                out.elements[ref] = Element(ref, role(node), name, node.isPassword, node)
            }
            if (gathered != null && !hasActionableDescendant(node, 0)) return
        } else {
            val text = ownName(node)
            if (text.isNotEmpty() && !node.isPassword) out.line(text.take(TEXT_CHARS))
        }
        for (i in 0 until node.childCount) {
            val child = child(node, i) ?: continue
            walk(child, out, depth + 1)
        }
    }

    private fun actionable(node: AccessibilityNodeInfo): Boolean =
        node.isEnabled && (node.isClickable || node.isEditable || node.isCheckable ||
            node.isScrollable || node.isLongClickable)

    private fun hasActionableDescendant(node: AccessibilityNodeInfo, depth: Int): Boolean {
        if (depth > 8) return false
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            if (!child.isVisibleToUser) continue
            if (actionable(child) || hasActionableDescendant(child, depth + 1)) return true
        }
        return false
    }

    /** I primi testi dentro un contenitore, per dargli un nome. */
    private fun gather(node: AccessibilityNodeInfo): List<String> {
        val found = mutableListOf<String>()
        val stack = ArrayDeque<AccessibilityNodeInfo>().apply { add(node) }
        var seen = 0
        while (stack.isNotEmpty() && found.size < 3 && seen < 40) {
            val n = stack.removeLast()
            seen++
            if (n !== node && n.isVisibleToUser && !n.isPassword) {
                ownName(n).takeIf { it.isNotEmpty() }?.let { found += it.take(40) }
            }
            for (i in n.childCount - 1 downTo 0) n.getChild(i)?.let { stack.add(it) }
        }
        return found
    }

    private fun ownName(node: AccessibilityNodeInfo): String =
        (node.text?.takeIf { it.isNotBlank() }
            ?: node.contentDescription?.takeIf { it.isNotBlank() }
            ?: node.hintText?.takeIf { it.isNotBlank() })
            ?.toString()?.replace('\n', ' ')?.trim().orEmpty()

    /** Il ruolo, detto con le parole del browser dove ce n'è una. */
    private fun role(node: AccessibilityNodeInfo): String {
        val cls = node.className?.toString().orEmpty().substringAfterLast('.')
        return when {
            node.isPassword -> "password"
            node.isEditable -> "textbox"
            cls.contains("Switch") -> "switch"
            cls.contains("CheckBox") -> "checkbox"
            cls.contains("RadioButton") -> "radio"
            node.isCheckable -> "checkbox"
            cls.contains("Button") -> "button"
            node.isClickable -> "button"
            node.isScrollable -> "list"
            else -> cls.lowercase().ifEmpty { "item" }
        }
    }

    private fun state(node: AccessibilityNodeInfo): String = buildString {
        if (node.isCheckable) append(if (node.isChecked) " checked" else " unchecked")
        if (node.isScrollable) append(" scrollable")
        if (node.isFocused) append(" focused")
        if (node.isPassword) append(if (node.text.isNullOrEmpty()) " empty" else " filled")
    }

    private fun child(node: AccessibilityNodeInfo, i: Int): AccessibilityNodeInfo? =
        if (Build.VERSION.SDK_INT >= 33) {
            node.getChild(i, AccessibilityNodeInfo.FLAG_PREFETCH_DESCENDANTS_DEPTH_FIRST)
        } else {
            node.getChild(i)
        }

    /**
     * La finestra vieta gli screenshot? Android non lo dice all'albero: lo si
     * scopre chiedendo uno screenshot della sola finestra e guardando il
     * rifiuto (API 34), come fa [ScreenSnapshots].
     */
    private fun isSecure(svc: AccessibilityService, window: AccessibilityWindowInfo, pkg: String): Boolean {
        if (Build.VERSION.SDK_INT < 34) return false
        val done = CountDownLatch(1)
        // Android limita la frequenza degli screenshot: una richiesta troppo
        // vicina alla precedente fallisce con un altro codice. In quel caso
        // vale la risposta di prima per la stessa app, mai un «non sicura»
        // inventato.
        var secure = lastSecure?.takeIf { it.first == pkg }?.second ?: false
        svc.takeScreenshotOfWindow(window.id, callbacks, object : AccessibilityService.TakeScreenshotCallback {
            override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                result.hardwareBuffer.close()
                secure = false
                done.countDown()
            }

            override fun onFailure(errorCode: Int) {
                if (errorCode == AccessibilityService.ERROR_TAKE_SCREENSHOT_SECURE_WINDOW) secure = true
                done.countDown()
            }
        })
        done.await(SECURE_PROBE_MS, TimeUnit.MILLISECONDS)
        lastSecure = pkg to secure
        return secure
    }

    private fun appLabel(svc: AccessibilityService, pkg: String): String = try {
        val pm = svc.packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
    } catch (e: Exception) {
        pkg
    }
}
