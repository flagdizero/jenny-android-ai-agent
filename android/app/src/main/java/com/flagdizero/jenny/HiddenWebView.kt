package com.flagdizero.jenny

import android.content.Context
import android.util.Log
import android.view.View
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView

/**
 * La WebView nascosta con cui l'agente naviga: la ricerca e il fetch
 * (`AgenticSearchBridge`) e la sessione di browser (`JennyBrowserBridge`).
 *
 * Le due la creavano con le stesse undici impostazioni e lo stesso User-Agent
 * copiato due volte: qui una volta. Resta a ciascuna quel che è suo — il
 * `WebViewClient` (la ricerca lo cambia a ogni chiamata, il browser ha il suo
 * filtro SSRF), il profilo separato del browser, e l'abilitazione del
 * debugging, che è di processo e sta in `AgenticSearchBridge`.
 */
object HiddenWebView {

    /** Un telefono qualunque: certi siti servono una pagina diversa, o un
     *  muro, a uno User-Agent da WebView. */
    const val USER_AGENT_MOBILE =
        "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"

    /**
     * JS acceso, niente immagini né zoom, niente finestre aperte dagli script,
     * mai contenuto misto; `GONE`. La console della pagina finisce in logcat
     * sotto *tag*, con la sorgente se *logSource* (alla ricerca serve sapere da
     * quale script viene un errore, al browser basta la riga).
     */
    fun create(context: Context, tag: String, logSource: Boolean = false): WebView =
        WebView(context).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.databaseEnabled = true
            settings.setSupportZoom(false)
            settings.builtInZoomControls = false
            settings.displayZoomControls = false
            settings.loadsImagesAutomatically = false
            settings.mediaPlaybackRequiresUserGesture = true
            settings.javaScriptCanOpenWindowsAutomatically = false
            settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.userAgentString = USER_AGENT_MOBILE
            visibility = View.GONE
            webChromeClient = object : WebChromeClient() {
                override fun onConsoleMessage(msg: ConsoleMessage?): Boolean {
                    val where = if (logSource) {
                        "${msg?.sourceId()}:${msg?.lineNumber()}"
                    } else {
                        "${msg?.lineNumber()}"
                    }
                    Log.d(tag, "JS console [$where] ${msg?.message()}")
                    return super.onConsoleMessage(msg)
                }
            }
        }
}
