package com.flagdizero.jenny

import android.graphics.Rect
import android.os.Build
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Dall'albero di accessibilità di una finestra a un testo che il modello possa
 * leggere: paragrafi, titoli, elenchi e il posto delle immagini.
 *
 * ## Due letture, secondo cosa c'è nella finestra
 *
 * * **Pagina web** — se la finestra contiene una `WebView` (Chrome, i browser
 *   Chromium, le app ibride) si legge *solo* lei: la barra degli indirizzi, i
 *   pulsanti e il «Translate page?» sono interfaccia del browser, non la
 *   pagina. Chrome mette su ogni nodo il suo ruolo nell'extra
 *   `AccessibilityNodeInfo.chromeRole` — `paragraph`, `heading`, `listItem`,
 *   `link`, `staticText`, `image`… — e con quello si ricompongono le righe: il
 *   testo di un paragrafo arriva spezzato in più nodi (`"La "`, `"potatura"`,
 *   `" è l'operazione…"`) e va riattaccato, non messo in colonna.
 * * **App nativa** — niente ruoli: ogni nodo con del testo è una riga.
 *
 * Misurato su `jenny_s22` (Chrome 154, 13/10/2026): una voce Wikipedia di
 * ~20.000 caratteri arriva **intera**, anche le parti fuori schermo, in
 * 1.300 nodi e ~0,3 s.
 *
 * ## Cosa non esce mai
 *
 * Il contenuto dei campi password (`isPassword`): al suo posto niente, nemmeno
 * i pallini.
 *
 * ## La lettura ha un orologio
 *
 * [render] si ferma a una scadenza, e si dichiara troncata. Non è un di più: la
 * chat flottante si apre allo scadere del tempo concesso alla lettura, e da quel
 * momento l'app sotto sparisce dalle finestre accessibili (v.
 * `FloatingOverlayController.expand`). Una visita che la scavalca non finisce
 * male, finisce **a pezzi**: misurato su `jenny_s22` il 13/10/2026 sulla voce
 * «Roma», ~800 ms di visita contro 700 di tetto davano 25k caratteri a un tocco
 * e 39k al successivo. Fermandosi prima si ha sempre l'inizio della pagina,
 * coerente, e per il resto Jenny ha l'indirizzo.
 */
object ScreenText {

    /** Tetto sul testo raccolto: oltre si tronca e lo si dice. Una pagina lunga
     *  sta in ~25k; il resto è un elenco infinito, e al modello non serve. */
    const val MAX_CHARS = 60_000

    /** Tetto sui nodi visitati, per la stessa ragione: ogni nodo è una
     *  chiamata fra processi. */
    private const val MAX_NODES = 8_000

    private const val EXTRA_ROLE = "AccessibilityNodeInfo.chromeRole"
    private const val EXTRA_TARGET_URL = "AccessibilityNodeInfo.targetUrl"

    /** Ruoli che aprono e chiudono una riga. Il resto (`staticText`, `link`,
     *  `superscript`, `abbr`, `listMarker`…) sta nella riga in cui cade. */
    private val BLOCK_ROLES = setOf(
        "paragraph", "heading", "listItem", "list", "genericContainer", "figure",
        "figcaption", "table", "row", "region", "section", "sectionWithoutName",
        "article", "main", "navigation", "banner", "contentInfo", "blockquote",
        "pre", "form", "dialog", "complementary", "header", "footer", "rootWebArea",
        "descriptionList", "term", "definition", "group",
    )

    /** Un'immagine trovata nella finestra. *bounds* è la parte visibile in
     *  coordinate schermo, vuota se l'immagine è fuori schermo. */
    class Image(val label: String, val url: String?, val bounds: Rect)

    class Result(val text: String, val images: List<Image>, val truncated: Boolean, val web: Boolean)

    private class Out(private val deadline: Long) {
        val sb = StringBuilder()
        val images = mutableListOf<Image>()
        var nodes = 0
        var truncated = false

        val full: Boolean
            get() = truncated || sb.length >= MAX_CHARS || nodes >= MAX_NODES ||
                SystemClock.elapsedRealtime() >= deadline

        fun inline(s: CharSequence) {
            if (full) { truncated = true; return }
            sb.append(s)
        }

        fun lineBreak() {
            if (sb.isNotEmpty() && sb[sb.length - 1] != '\n') sb.append('\n')
        }
    }

    /** *deadline* in `SystemClock.elapsedRealtime()`: oltre, ci si ferma. */
    fun render(root: AccessibilityNodeInfo, deadline: Long = Long.MAX_VALUE): Result {
        val out = Out(deadline)
        val webRoots = mutableListOf<AccessibilityNodeInfo>()
        findWebViews(root, webRoots, 0)
        if (webRoots.isNotEmpty()) {
            for (web in webRoots) {
                renderWeb(web, out, 0)
                out.lineBreak()
            }
        } else {
            renderNative(root, out, 0)
        }
        return Result(normalize(out.sb), out.images, out.truncated, webRoots.isNotEmpty())
    }

    private fun findWebViews(node: AccessibilityNodeInfo, found: MutableList<AccessibilityNodeInfo>, depth: Int) {
        if (depth > 60) return
        if (node.className?.toString() == "android.webkit.WebView") {
            found += node
            return
        }
        for (i in 0 until node.childCount) {
            val child = child(node, i) ?: continue
            findWebViews(child, found, depth + 1)
        }
    }

    private fun renderWeb(node: AccessibilityNodeInfo, out: Out, depth: Int) {
        if (out.full || depth > 200) { out.truncated = out.truncated || out.full; return }
        out.nodes++
        if (node.isPassword) return
        val role = node.extras?.getCharSequence(EXTRA_ROLE)?.toString()
        if (role == "image") {
            addImage(node, out, label(node))
            return
        }
        val block = role != null && role in BLOCK_ROLES
        if (block) out.lineBreak()
        if (role == "heading") out.inline("## ")
        val before = out.sb.length
        val text = node.text
        if (node.childCount == 0) {
            // Anche uno spazio da solo conta: è quello che separa «3» da
            // «Esecuzione dei tagli» in una voce d'indice.
            val own = if (!text.isNullOrEmpty()) text else node.contentDescription
            if (!own.isNullOrEmpty()) out.inline(own)
        } else {
            for (i in 0 until node.childCount) {
                val child = child(node, i) ?: continue
                renderWeb(child, out, depth + 1)
            }
            // Un link o un pulsante senza testo nei figli si legge dalla sua
            // descrizione; con testo nei figli la descrizione è lo stesso testo
            // ripetuto, e si salta.
            if (out.sb.length == before) {
                val own = text?.takeIf { it.isNotBlank() } ?: node.contentDescription
                if (!own.isNullOrBlank()) out.inline(own)
            }
        }
        if (block) out.lineBreak()
    }

    private fun renderNative(node: AccessibilityNodeInfo, out: Out, depth: Int) {
        if (out.full || depth > 200) { out.truncated = out.truncated || out.full; return }
        out.nodes++
        if (node.isPassword) return
        val cls = node.className?.toString().orEmpty()
        if (cls.endsWith("ImageView") || cls.endsWith(".Image")) {
            val desc = node.contentDescription
            if (!desc.isNullOrBlank()) addImage(node, out, desc.toString())
            // Un'immagine senza descrizione non dice niente: non la si conta.
        } else {
            val text = node.text?.takeIf { it.isNotBlank() }
                ?: node.contentDescription?.takeIf { it.isNotBlank() && node.childCount == 0 }
            if (text != null) {
                out.lineBreak()
                out.inline(text)
                out.lineBreak()
            }
        }
        for (i in 0 until node.childCount) {
            val child = child(node, i) ?: continue
            renderNative(child, out, depth + 1)
        }
    }

    /**
     * Il figlio *i*, chiedendo al sistema di portarsi dietro anche i suoi
     * discendenti (API 33+): ogni nodo è una chiamata fra processi, e in blocco
     * se ne fanno molte meno.
     */
    private fun child(node: AccessibilityNodeInfo, i: Int): AccessibilityNodeInfo? =
        if (Build.VERSION.SDK_INT >= 33) {
            node.getChild(i, AccessibilityNodeInfo.FLAG_PREFETCH_DESCENDANTS_DEPTH_FIRST)
        } else {
            node.getChild(i)
        }

    private fun label(node: AccessibilityNodeInfo): String =
        (node.contentDescription?.takeIf { it.isNotBlank() } ?: node.text?.takeIf { it.isNotBlank() })
            ?.toString()?.trim().orEmpty()

    private fun addImage(node: AccessibilityNodeInfo, out: Out, label: String) {
        val bounds = Rect()
        if (node.isVisibleToUser) node.getBoundsInScreen(bounds)
        val url = node.extras?.getCharSequence(EXTRA_TARGET_URL)?.toString()?.takeIf { it.isNotBlank() }
        out.images += Image(label, url, bounds)
        val n = out.images.size
        out.inline(if (label.isEmpty()) " [image $n] " else " [image $n: $label] ")
    }

    /** Spazi doppi via, righe vuote al massimo una, niente spazi a fine riga. */
    private fun normalize(sb: StringBuilder): String = sb.toString()
        .replace(' ', ' ')
        .lines()
        .map { it.replace(Regex(" {2,}"), " ").trim() }
        .joinToString("\n")
        .replace(Regex("\n{3,}"), "\n\n")
        .trim()
}
