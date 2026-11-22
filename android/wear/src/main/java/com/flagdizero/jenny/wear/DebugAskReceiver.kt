package com.flagdizero.jenny.wear

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Fa come se si fosse detta una frase, per le prove via adb: l'emulatore Wear
 * non ha il riconoscitore vocale, e senza questo il filo non si proverebbe che
 * sul polso vero.
 *
 * ```
 * adb shell am broadcast -n com.flagdizero.jenny/com.flagdizero.jenny.wear.DebugAskReceiver \
 *     -a com.flagdizero.jenny.wear.DEBUG_ASK --es testo "'come stanno le piante'"
 * ```
 *
 * Con `--es vista pronta|ascolto|penso|risposta|errore` mostra quella schermata
 * con `testo` dentro, senza chiedere niente al telefono: serve a guardarle.
 *
 * Con `--es voce_mia imposta --es nome … --es url … --es chiave … --es corpo '{…}'`
 * Jenny parla con un sintetizzatore in rete invece che con la voce
 * dell'orologio (vedi [OwnVoice]). Le voci salvate possono essere più d'una:
 * `--es voce_mia usa --es nome …` sceglie quale, `--es voce_mia spegni` torna
 * alla voce dell'orologio senza cancellarle, `--es voce_mia togli --es nome …`
 * ne cancella una, `--es voce_mia elenco` le scrive nel log.
 *
 * Nelle build di release non fa niente; il manifest lo protegge con `DUMP`,
 * che ha la shell e nessuna app normale.
 */
class DebugAskReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!BuildConfig.DEBUG) return
        val text = intent.getStringExtra("testo").orEmpty()
        // Con `--es vista`, la schermata si mostra e basta: niente giro col telefono.
        val view = intent.getStringExtra("vista")
        // Con `--es voci elenco` le voci vanno nel log; con `--es voci <nome>` se ne sente una,
        // e con `--es file <nome>` in più finisce in quel file della cache invece che in aria.
        val voice = intent.getStringExtra("voci")
        val own = intent.getStringExtra("voce_mia")
        if (own != null) {
            val name = intent.getStringExtra("nome").orEmpty()
            val ok = when (own) {
                "imposta" -> OwnVoice.configure(
                    context, name, intent.getStringExtra("url").orEmpty(),
                    intent.getStringExtra("chiave"), intent.getStringExtra("corpo"),
                )
                "usa" -> OwnVoice.use(context, name)
                "spegni" -> OwnVoice.use(context, null)
                "togli" -> OwnVoice.remove(context, name).let { true }
                else -> own == "elenco"
            }
            Log.i("JennyWear", "Own voice: $own ok=$ok saved=${OwnVoice.names(context)} in use=${OwnVoice.active(context) ?: "the watch's"}")
            return
        }
        val ok = when {
            voice != null -> AskActivity.debugVoice(voice.takeIf { it != "elenco" }, text, intent.getFloatExtra("tono", 1.25f), intent.getStringExtra("file"))
            view != null -> AskActivity.debugShow(view, text)
            else -> text.isNotBlank() && AskActivity.debugAsk(text)
        }
        Log.i("JennyWear", "Debug ask delivered=$ok (chars=${text.length})")
    }
}
