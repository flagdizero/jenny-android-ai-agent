package com.flagdizero.jenny.wear

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Handler
import android.util.Log
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Una voce propria: invece del motore vocale dell'orologio, Jenny parla con un
 * sintetizzatore in rete scelto da chi porta l'orologio.
 *
 * Non c'è nessun servizio scritto qui dentro. Le voci stanno sull'orologio,
 * nelle preferenze dell'app: se ne possono tenere più d'una, ognuna col suo
 * nome, e una sola è quella in uso ([use]). Una voce è fatta di tre cose: l'indirizzo a cui mandare
 * la richiesta, la chiave (se serve, va come `Authorization: Bearer`) e il
 * corpo JSON della richiesta, dove il valore `"{{text}}"` viene sostituito con
 * la frase da dire. La risposta deve essere l'audio stesso. Senza impostazione
 * — cioè in ogni installazione normale — questa classe non fa niente e non apre
 * connessioni.
 *
 * La chiave non passa dal codice né dalla build: la si consegna una volta
 * all'orologio ([configure]) e si toglie con [remove].
 *
 * Tutti i metodi si chiamano dal main thread; la rete gira su un thread suo.
 */
class OwnVoice(private val context: Context, private val main: Handler) {

    private var player: MediaPlayer? = null

    /** Cresce a ogni frase e a ogni stop: l'audio di una frase vecchia si butta. */
    @Volatile
    private var turn = 0

    /** I pezzi già scaricati che aspettano di essere suonati, in ordine. */
    private val ready = ArrayDeque<File>()

    /** Tutti i pezzi sono arrivati; oppure uno è mancato, e [missing] è il testo da lì in poi. */
    private var fetchedAll = false
    private var missing: String? = null
    private var started = false

    val configured: Boolean get() = active(context) != null

    /**
     * Dice [text]. [onStart] quando l'audio comincia, [onEnd] quando finisce,
     * [onFail] se il sintetizzatore non ha risposto, con il testo che resta da
     * dire: allora lo dice qualcun altro. Una frase fermata nel frattempo non
     * chiama nessuno dei tre.
     *
     * Un testo lungo va al sintetizzatore a pezzi, una frase o poche alla
     * volta: un servizio ha un tetto alla lunghezza, e così la voce parte dopo
     * il primo pezzo invece di aspettare tutto l'audio. I pezzi dopo si
     * scaricano mentre suonano quelli prima.
     */
    fun speak(text: String, onStart: () -> Unit, onEnd: () -> Unit, onFail: (String) -> Unit) {
        stop()
        val mine = turn
        val p = prefs(context)
        val name = active(context) ?: return onFail(text)
        val url = p.getString(KEY_URL + name, null) ?: return onFail(text)
        val token = p.getString(KEY_TOKEN + name, null)
        val template = p.getString(KEY_BODY + name, null).orEmpty()
        val pieces = Spoken.pieces(text, *PIECE_CHARS)
        if (pieces.isEmpty() || fill(template, "x") == null) return onFail(text)
        val next = { next(mine, onStart, onEnd, onFail) }
        // Ogni frase ha i suoi file: uno scaricamento vecchio che finisce tardi
        // non scrive sopra quelli nuovi. Quelli rimasti dalle frasi prima si buttano.
        context.cacheDir.listFiles { f -> f.name.startsWith("own-voice-") }?.forEach { it.delete() }
        Thread {
            for ((i, piece) in pieces.withIndex()) {
                if (mine != turn) return@Thread
                val file = File(context.cacheDir, "own-voice-$mine-$i.audio")
                val began = System.currentTimeMillis()
                val ok = fetch(url, token, fill(template, piece).orEmpty(), file)
                Log.i(TAG, "Own voice '$name': piece ${i + 1}/${pieces.size} ok=$ok in " +
                    "${System.currentTimeMillis() - began} ms (chars=${piece.length})")
                main.post {
                    if (mine != turn) return@post
                    if (ok) ready += file else missing = pieces.drop(i).joinToString(" ")
                    if (i == pieces.lastIndex) fetchedAll = true
                    if (player == null) next()
                }
                if (!ok) return@Thread
            }
        }.start()
    }

    fun stop() {
        turn++
        release()
        ready.clear()
        fetchedAll = false
        missing = null
        started = false
    }

    private fun release() {
        player?.let {
            it.setOnCompletionListener(null)
            it.release()
        }
        player = null
    }

    /** Suona il prossimo pezzo pronto; se non ce ne sono più, chiude o passa la mano. */
    private fun next(mine: Int, onStart: () -> Unit, onEnd: () -> Unit, onFail: (String) -> Unit) {
        if (mine != turn) return
        release()
        val file = ready.removeFirstOrNull()
        if (file == null) {
            val rest = missing
            when {
                rest != null -> {
                    stop()
                    onFail(rest)
                }
                fetchedAll -> {
                    stop()
                    onEnd()
                }
                // Altrimenti il pezzo dopo è ancora in viaggio: lo suona chi lo riceve.
            }
            return
        }
        val mp = MediaPlayer()
        player = mp
        val again = { next(mine, onStart, onEnd, onFail) }
        try {
            mp.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            mp.setDataSource(file.path)
            mp.setOnPreparedListener {
                if (mine != turn) return@setOnPreparedListener
                it.start()
                if (!started) {
                    started = true
                    onStart()
                }
            }
            mp.setOnCompletionListener {
                file.delete()
                again()
            }
            mp.setOnErrorListener { _, what, _ ->
                Log.i(TAG, "Own voice: the audio does not play ($what)")
                if (mine == turn) again()
                true
            }
            mp.prepareAsync()
        } catch (e: IOException) {
            Log.i(TAG, "Own voice: the audio does not open (${e.javaClass.simpleName})")
            again()
        }
    }

    /** Chiede l'audio e lo scrive in [file]; `false` per qualunque intoppo. */
    private fun fetch(url: String, token: String?, body: String, file: File): Boolean {
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL(url).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = CONNECT_MS
                readTimeout = READ_MS
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                if (!token.isNullOrBlank()) setRequestProperty("Authorization", "Bearer $token")
            }
            conn.outputStream.use { it.write(body.toByteArray()) }
            val code = conn.responseCode
            val type = conn.contentType.orEmpty()
            // Un errore del servizio torna spesso come JSON con stato 200: non è audio.
            if (code !in 200..299 || type.startsWith("application/json") || type.startsWith("text/")) {
                Log.i(TAG, "Own voice: refused (http=$code type=$type)")
                return false
            }
            conn.inputStream.use { input -> file.outputStream().use { input.copyTo(it) } }
            file.length() > 0
        } catch (e: IOException) {
            Log.i(TAG, "Own voice: unreachable (${e.javaClass.simpleName})")
            false
        } catch (e: RuntimeException) {
            Log.i(TAG, "Own voice: bad setting (${e.javaClass.simpleName})")
            false
        } finally {
            conn?.disconnect()
        }
    }

    companion object {
        private const val TAG = "JennyWear"
        private const val KEY_ACTIVE = "own_voice_active"
        private const val KEY_URL = "own_voice_url_"
        private const val KEY_TOKEN = "own_voice_token_"
        private const val KEY_BODY = "own_voice_body_"
        private const val PLACEHOLDER = "{{text}}"
        private const val CONNECT_MS = 5_000
        private const val READ_MS = 20_000

        /**
         * Quanto sono lunghi il primo pezzo, il secondo e gli altri. Un
         * sintetizzatore ci mette un paio di secondi più un terzo del tempo
         * che l'audio dura: il secondo pezzo deve arrivare mentre suona il
         * primo, che è corto, quindi è corto anche lui.
         */
        private val PIECE_CHARS = intArrayOf(160, 220, 400)

        private fun prefs(context: Context) = context.getSharedPreferences("jenny_wear", Context.MODE_PRIVATE)

        /** Il corpo della richiesta con [text] al posto di `"{{text}}"`; `null` se non è JSON o il posto manca. */
        fun fill(template: String, text: String): String? = try {
            val json = JSONObject(template)
            val keys = json.keys().asSequence().filter { json.opt(it) == PLACEHOLDER }.toList()
            if (keys.isEmpty()) null else json.apply { keys.forEach { put(it, text) } }.toString()
        } catch (_: JSONException) {
            null
        }

        /** I nomi delle voci salvate. */
        fun names(context: Context): List<String> =
            prefs(context).all.keys.filter { it.startsWith(KEY_URL) }.map { it.removePrefix(KEY_URL) }.sorted()

        /** La voce in uso, o `null` se parla l'orologio. */
        fun active(context: Context): String? =
            prefs(context).getString(KEY_ACTIVE, null)?.takeIf { prefs(context).contains(KEY_URL + it) }

        /**
         * Salva la voce [name] e la mette in uso; `false` se il nome non è fatto
         * di lettere e cifre, l'indirizzo non è https o il corpo non ha il
         * posto per la frase.
         */
        fun configure(context: Context, name: String, url: String, token: String?, body: String?): Boolean {
            if (!NAME.matches(name) || !url.startsWith("https://") || fill(body.orEmpty(), "x") == null) return false
            prefs(context).edit()
                .putString(KEY_URL + name, url).putString(KEY_TOKEN + name, token.orEmpty())
                .putString(KEY_BODY + name, body).putString(KEY_ACTIVE, name)
                .apply()
            return true
        }

        /** Mette in uso la voce [name]; con `null` torna a parlare l'orologio. `false` se quella voce non c'è. */
        fun use(context: Context, name: String?): Boolean {
            if (name != null && name !in names(context)) return false
            prefs(context).edit().apply { if (name == null) remove(KEY_ACTIVE) else putString(KEY_ACTIVE, name) }.apply()
            return true
        }

        /** Cancella la voce [name], chiave compresa; se era in uso, torna a parlare l'orologio. */
        fun remove(context: Context, name: String) {
            prefs(context).edit().remove(KEY_URL + name).remove(KEY_TOKEN + name).remove(KEY_BODY + name).apply()
        }

        private val NAME = Regex("[a-z0-9]{1,20}")
    }
}
