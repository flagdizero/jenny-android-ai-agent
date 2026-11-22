package com.flagdizero.jenny.wearprotocol

import org.json.JSONObject

/**
 * Il protocollo fra Jenny sul telefono e Jenny sull'orologio, scritto una volta.
 *
 * Questo file è compilato **in tutte e due le app**: il modulo `:wear` e il
 * flavor `play` di `:app` lo aggiungono ai propri sorgenti (v. i due
 * `build.gradle.kts`). È il motivo per cui esiste invece di due copie: i due
 * APK si aggiornano separatamente, e una stringa di percorso che diverge fra
 * telefono e orologio non rompe la compilazione — fa sparire i messaggi.
 *
 * Il giro, per la prima versione:
 *
 * 1. orologio → telefono, [PATH_ASK]: `{"v":1, "id":"…", "text":"…"}`;
 * 2. telefono → orologio, [PATH_ACK]: `{"v":1, "id":"…", "ok":true}` appena il
 *    gateway ha accettato la domanda, oppure `"ok":false` con un [Ack.error];
 * 3. telefono → orologio, [PATH_REPLY]: `{"v":1, "id":"…", "text":"…"}` quando
 *    Jenny ha finito il turno — il testo intero, mai a pezzi.
 *
 * **La versione c'è dal primo messaggio**, perché telefono e orologio si
 * aggiornano separatamente e prima o poi parleranno due versioni diverse. Chi
 * riceve una versione che non conosce lo dice ([ERR_VERSION]) invece di
 * indovinare.
 */
object WearProtocol {
    const val VERSION = 1

    const val PATH_ASK = "/jenny/ask"
    const val PATH_ACK = "/jenny/ack"
    const val PATH_REPLY = "/jenny/reply"

    /** La capability che il telefono dichiara (`res/values/wear.xml` del flavor
     *  `play`) e che l'orologio cerca per sapere a quale nodo parlare. */
    const val CAPABILITY_PHONE = "jenny_phone"

    /** Tetto del testo di una domanda. Lo stesso di `MAX_TEXT_CHARS` in
     *  `jenny/runtime/native_input.py`: oltre, Python la rifiuterebbe. */
    const val MAX_TEXT_CHARS = 2000

    /** Quanto l'orologio aspetta la conferma. Sopra il budget di consegna del
     *  telefono (`GatewayService.WEAR_DELIVERY_BUDGET_MS`, 20 s), così un
     *  gateway che si sta alzando ha il tempo di dire sì o no. */
    const val ACK_TIMEOUT_MS = 30_000L

    /** Quanto l'orologio aspetta la risposta dopo la conferma. Un turno con
     *  qualche tool può durare minuti; oltre questo l'orologio smette di
     *  aspettare e lo dice — la risposta resta comunque nella chat. */
    const val REPLY_TIMEOUT_MS = 180_000L

    /** La versione del messaggio non è [VERSION]. */
    const val ERR_VERSION = "version"
    /** Il messaggio non si legge (JSON rotto, campi mancanti, testo vuoto). */
    const val ERR_MALFORMED = "malformed"
    /** Testo oltre [MAX_TEXT_CHARS]. */
    const val ERR_TOO_LONG = "too_long"
    /** Il gateway non ha accettato la domanda: spento e non rialzabile, in
     *  avvio oltre il budget, o rifiutata da Python. */
    const val ERR_UNREACHABLE = "unreachable"

    data class Ask(val version: Int, val id: String, val text: String)
    data class Ack(val version: Int, val id: String, val ok: Boolean, val error: String?)
    data class Reply(val version: Int, val id: String, val text: String)

    fun encodeAsk(id: String, text: String): ByteArray =
        JSONObject().put("v", VERSION).put("id", id).put("text", text).bytes()

    fun encodeAck(id: String, ok: Boolean, error: String? = null): ByteArray =
        JSONObject().put("v", VERSION).put("id", id).put("ok", ok)
            .apply { if (error != null) put("error", error) }
            .bytes()

    fun encodeReply(id: String, text: String): ByteArray =
        JSONObject().put("v", VERSION).put("id", id).put("text", text).bytes()

    /** `null` se il messaggio non si legge. La versione si legge sempre per
     *  prima, anche quando il resto è sconosciuto: è ciò che permette di
     *  rispondere [ERR_VERSION] invece di [ERR_MALFORMED]. */
    fun decodeAsk(data: ByteArray): Ask? = decode(data) { json, v, id ->
        Ask(v, id, json.optString("text", ""))
    }

    fun decodeAck(data: ByteArray): Ack? = decode(data) { json, v, id ->
        Ack(v, id, json.optBoolean("ok", false), json.optString("error", "").ifEmpty { null })
    }

    fun decodeReply(data: ByteArray): Reply? = decode(data) { json, v, id ->
        Reply(v, id, json.optString("text", ""))
    }

    private fun <T> decode(data: ByteArray, build: (JSONObject, Int, String) -> T): T? = try {
        val json = JSONObject(String(data, Charsets.UTF_8))
        val id = json.optString("id", "")
        if (id.isEmpty()) null else build(json, json.optInt("v", -1), id)
    } catch (_: Exception) {
        null
    }

    private fun JSONObject.bytes(): ByteArray = toString().toByteArray(Charsets.UTF_8)
}
