package com.flagdizero.jenny

import android.system.Os
import android.util.Base64
import android.util.Log
import com.jcraft.jsch.ChannelExec
import com.jcraft.jsch.ChannelSftp
import com.jcraft.jsch.HostKey
import com.jcraft.jsch.HostKeyRepository
import com.jcraft.jsch.JSch
import com.jcraft.jsch.KeyPair
import com.jcraft.jsch.Session
import com.jcraft.jsch.UIKeyboardInteractive
import com.jcraft.jsch.UserInfo
import org.bouncycastle.jce.provider.BouncyCastleProvider
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.io.InputStreamReader
import java.net.SocketTimeoutException
import java.security.MessageDigest
import java.security.Security
import javax.crypto.Cipher

/**
 * Errore gia classificato dal bridge: la categoria arriva a Python senza
 * passare dall'euristica sui messaggi di jsch.
 */
private class BridgeException(val category: String, message: String) : Exception(message)

/**
 * Legge uno stream fino a EOF tenendo solo i primi [limit] CARATTERI.
 *
 * Il taglio avviene qui e non lato Python perche l'output di un comando remoto
 * puo essere di megabyte: farlo attraversare il confine JNI per poi buttarlo
 * via costerebbe la copia (e la memoria) di tutto quanto. Il conteggio e in
 * caratteri, non in byte, per coincidere con il backend dev — che tronca la
 * stringa gia decodificata.
 */
private class StreamCollector(
    private val input: InputStream,
    private val limit: Int,
) : Thread() {

    val text = StringBuilder()
    var discarded: Long = 0
        private set

    init {
        isDaemon = true
    }

    override fun run() {
        try {
            val reader = InputStreamReader(input, Charsets.UTF_8)
            val buffer = CharArray(4096)
            while (true) {
                val read = reader.read(buffer)
                if (read < 0) break
                val room = limit - text.length
                if (room <= 0) {
                    discarded += read
                    continue
                }
                val kept = minOf(room, read)
                text.append(buffer, 0, kept)
                discarded += (read - kept)
            }
        } catch (e: Throwable) {
            // Lo stream viene chiuso da disconnect() quando il comando scade:
            // l'eccezione qui e la fine della lettura, non un errore da propagare.
        }
    }
}

/**
 * Bridge SSH nativo (jsch) esposto a Python via Chaquopy.
 *
 * Confine deliberatamente stretto: **JSON string in, JSON string out**, come
 * [AgenticSearchBridge]. Nessun tipo complesso, nessun array di byte — SFTP
 * riceve dei PATH e apre i file da se. Il pool di [Session] vive qui, indicizzato
 * per `poolKey` (l'impronta dei parametri di connessione calcolata da Python):
 * se l'utente corregge porta o utente la chiave cambia e la sessione viene
 * riaperta invece di riusare quella vecchia.
 *
 * Ogni eccezione Java viene catturata e tradotta in `{"error": ..., "category": ...}`
 * con una categoria fra `host_key`, `auth`, `timeout`, `io`: lato Python quelle
 * quattro diventano eccezioni diverse, perche una host key non pinnata richiede
 * un intervento umano e un timeout no.
 *
 * Il lato Python e `jenny/agent/tools/ssh_backends/android.py`.
 */
object SshBridge {

    private const val TAG = "SshBridge"

    // Sessioni vive, per poolKey. Le chiamate arrivano da `asyncio.to_thread`,
    // quindi da thread diversi e potenzialmente in parallelo: la mappa e il
    // lock per-chiave servono davvero.
    private val sessions = HashMap<String, Session>()
    private val locks = HashMap<String, Any>()

    // Ultimo uso per poolKey (nanoTime, monotono) e il thread che pota le
    // sessioni ferme: vedi startIdleReaper.
    private val lastUsed = HashMap<String, Long>()
    private var reaper: Thread? = null

    // ---- provider crittografico -------------------------------------------

    /** Nome del provider che serve AES/GCM in questo momento, o l'errore. */
    private fun aesGcmProvider(): String =
        try {
            Cipher.getInstance("AES/GCM/NoPadding").provider.name
        } catch (e: Throwable) {
            "ERROR: ${e.javaClass.simpleName}: ${e.message}"
        }

    /**
     * Registra BouncyCastle **in fondo** alla lista dei provider.
     *
     * Due vincoli che si scontrano. Il primo: Android spedisce un provider che
     * si chiama gia "BC" e che NON ha Ed25519, quindi `addProvider` da solo non
     * farebbe nulla — in silenzio — perche il nome e occupato. Serve rimuovere
     * quello ridotto prima.
     *
     * Il secondo, ed e il motivo per cui qui si usa `addProvider` e NON
     * `insertProviderAt(bc, 1)`: la posizione decide chi serve *tutti* gli
     * algoritmi, non solo quelli che ci servono. Misurato sul dispositivo
     * (Titan 2, Android 16): con BouncyCastle in posizione 1 il provider di
     * AES/GCM passava da `AndroidOpenSSL` a `BC` per l'INTERA app, compreso il
     * container di backup cifrato (`jenny/snapshot/crypto_backends/android.py`).
     * Due danni in uno: un cambio di implementazione sotto i piedi al backup, e
     * la perdita dell'accelerazione hardware di BoringSSL a favore del Java puro
     * di BouncyCastle — su un archivio di tutto il workspace si sente.
     *
     * In coda invece BouncyCastle serve solo cio che nessun altro provider
     * offre: JCA scorre la lista in ordine di priorita e arriva a lui per
     * Ed25519, mentre AES/GCM resta ad AndroidOpenSSL. Il JSON riporta il
     * provider di AES/GCM prima e dopo proprio per rendere questa proprieta
     * verificabile invece che sperata: se `aesGcmProviderAfter` non coincide
     * con `aesGcmProviderBefore`, qualcuno ha rimesso l'inserimento in testa.
     *
     * Idempotente e sincronizzata: chiamarla due volte non fa nulla la seconda.
     */
    @JvmStatic
    @Synchronized
    fun installProvider(): String {
        val out = JSONObject()
        val before = aesGcmProvider()
        out.put("aesGcmProviderBefore", before)
        try {
            val existing = Security.getProvider("BC")
            out.put("existingBcClass", existing?.javaClass?.name ?: "none")
            val alreadyFull = existing is BouncyCastleProvider
            out.put("alreadyFull", alreadyFull)
            if (!alreadyFull) {
                // removeProvider ritorna void: l'esito si legge rileggendo.
                Security.removeProvider("BC")
                out.put("removedOk", Security.getProvider("BC") == null)
                // In CODA: -1 significa "non inserito, nome ancora occupato".
                out.put("insertedAt", Security.addProvider(BouncyCastleProvider()))
            }
            val bc = Security.getProvider("BC")
            out.put("bcClass", bc?.javaClass?.name ?: "none")
            out.put("bcVersion", bc?.version?.toString() ?: "none")
            out.put("ok", bc is BouncyCastleProvider)
        } catch (e: Throwable) {
            out.put("ok", false)
            out.put("error", "${e.javaClass.simpleName}: ${e.message}")
        }
        val after = aesGcmProvider()
        out.put("aesGcmProviderAfter", after)
        // Invariante, non decorazione: se cambia, il backup cifrato ha cambiato
        // implementazione sotto i piedi senza che nessuno lo abbia chiesto.
        val unchanged = before == after
        out.put("aesGcmUnchanged", unchanged)
        if (!unchanged) {
            // A livello WARN e non INFO: e una regressione silenziosa, e l'unico
            // modo di accorgersene senza saperla cercare e che si veda da sola.
            Log.w(
                TAG,
                "AES/GCM provider changed ($before -> $after): the encrypted " +
                    "backup container now uses a different implementation. " +
                    "Verify a backup export/import round trip.",
            )
        }
        return out.toString()
    }

    // ---- traduzione degli errori ------------------------------------------

    /**
     * Categoria dell'errore: `host_key`, `auth`, `timeout` o `io`.
     *
     * jsch non ha una gerarchia di eccezioni utile — quasi tutto e una
     * `JSchException` — quindi la distinzione passa dal messaggio. Il match e
     * volutamente largo: sbagliare classificando come `io` un problema di host
     * key sarebbe grave (l'agente riproverebbe invece di chiedere all'utente),
     * quindi le due categorie che richiedono un umano si testano per prime.
     */
    private fun categoryOf(error: Throwable): String {
        var current: Throwable? = error
        while (current != null) {
            if (current is BridgeException) return current.category
            if (current is SocketTimeoutException) return "timeout"
            current = current.cause
        }
        val text = collectMessages(error).lowercase()
        return when {
            "hostkey" in text || "host key" in text -> "host_key"
            "auth fail" in text || "auth cancel" in text || "userauth" in text -> "auth"
            "timeout" in text || "timed out" in text -> "timeout"
            else -> "io"
        }
    }

    private fun collectMessages(error: Throwable): String {
        val parts = StringBuilder()
        var current: Throwable? = error
        while (current != null) {
            parts.append(current.javaClass.simpleName).append(": ")
            parts.append(current.message ?: "").append(" | ")
            current = current.cause
        }
        return parts.toString()
    }

    private fun errorJson(error: Throwable): JSONObject {
        val message = if (error is BridgeException) {
            error.message ?: "ssh bridge error"
        } else {
            "${error.javaClass.simpleName}: ${error.message ?: "no message"}"
        }
        return JSONObject().put("error", message).put("category", categoryOf(error))
    }

    /** Esegue [body] traducendo qualunque eccezione in un JSON con categoria. */
    private inline fun respond(body: () -> JSONObject): String =
        try {
            body().toString()
        } catch (e: Throwable) {
            // Solo classe e messaggio: gli argomenti (comandi, path, e per gli
            // host a password la PASSWORD) non finiscono nel log di sistema.
            // Niente `request` qui dentro, mai: sarebbe una password leggibile
            // con `adb logcat` da qualunque app con quel permesso.
            Log.w(TAG, "ssh call failed: ${e.javaClass.simpleName}: ${e.message}")
            errorJson(e).toString()
        }

    /**
     * Decodifica la richiesta senza rimetterne il testo nell'errore.
     *
     * `JSONObject(String)` di org.json cita l'input nel messaggio d'eccezione
     * ("Unterminated string at character 42 of {...}"), e quel messaggio
     * risalirebbe a Python dentro `error` — cioe fino al risultato di un tool,
     * cioe nel contesto del modello. Il payload contiene la password: qui si
     * scarta il dettaglio e si risponde con una frase fissa.
     */
    private fun parseRequest(request: String): JSONObject =
        try {
            JSONObject(request)
        } catch (e: Throwable) {
            throw BridgeException("io", "the ssh bridge received a malformed request")
        }

    // ---- pool di sessioni --------------------------------------------------

    private fun lockFor(key: String): Any = synchronized(locks) { locks.getOrPut(key) { Any() } }

    private fun openSession(request: JSONObject): Session {
        val host = request.getString("host")
        val port = request.getInt("port")
        val keyPath = request.getString("keyPath")
        val knownHosts = request.getString("knownHostsPath")
        // `isNull` copre sia la chiave assente sia un JSON null: Python omette il
        // campo quando non serve, e `optString` su un null di org.json
        // restituirebbe la STRINGA "null", che jsch userebbe come password.
        val password = if (request.isNull("password")) null else request.getString("password")

        if (password == null && !File(keyPath).isFile) {
            // Solo per l'auth a chiave: un host a password non ha un file di chiave.
            throw BridgeException("io", "private key $keyPath is missing")
        }
        if (!File(knownHosts).isFile) {
            // Senza il file non c'e nulla di pinnato: e un errore da umano, non
            // da riprovare, esattamente come una host key sconosciuta. Il
            // controllo NON ha un ramo che lo salti per l'auth a password: e
            // proprio li che conta di piu, perche senza impronta verificata la
            // password andrebbe in chiaro a chiunque risponda a quell'indirizzo,
            // mentre una chiave privata non lascia comunque il telefono.
            throw BridgeException("host_key", "no known_hosts file at $knownHosts")
        }

        val jsch = JSch()
        jsch.setKnownHosts(knownHosts)
        if (password == null) {
            jsch.addIdentity(keyPath)
        }
        val session = jsch.getSession(request.getString("username"), host, port)
        // Nessun TOFU: un host assente da known_hosts non si contatta. Vale per
        // entrambi i modi di autenticazione — vedi il commento qui sopra.
        session.setConfig("StrictHostKeyChecking", "yes")
        // Solo i metodi coerenti col modo dichiarato in Settings. Senza questo
        // jsch prova anche gli altri: con l'auth a chiave tenterebbe password
        // e keyboard-interactive, e con l'auth a password tenterebbe publickey
        // per primo, bruciando tentativi su un server con `MaxAuthTries` basso.
        //
        // Nel modo password si dichiara ANCHE keyboard-interactive, e non e un
        // di piu: il tipico Linux che autentica via PAM ha
        // `PasswordAuthentication no` + `KbdInteractiveAuthentication yes`, e
        // con la sola "password" rifiuterebbe la connessione. asyncssh negozia
        // kbdint da solo, quindi senza questa riga il backend dev e il bridge
        // NON sarebbero in parita: stesso server, funziona sul Mac e fallisce
        // sul telefono.
        session.setConfig(
            "PreferredAuthentications",
            if (password != null) "password,keyboard-interactive" else "publickey",
        )
        if (password != null) {
            session.setPassword(password)
        }
        // Con la password serve un UserInfo che sappia rispondere ai prompt
        // kbdint; senza credenziale resta quello muto.
        session.userInfo =
            if (password != null) CredentialUserInfo(password) else SilentUserInfo
        val keepalive = request.optInt("keepaliveIntervalS", 0)
        if (keepalive > 0) {
            session.serverAliveInterval = keepalive * 1000
            session.serverAliveCountMax = 3
        }
        session.connect((request.optDouble("connectTimeoutS", 15.0) * 1000).toInt())
        return session
    }

    /** Sessione viva per questo `poolKey`, riaperta se caduta. */
    private fun sessionFor(request: JSONObject): Session {
        val key = request.getString("poolKey")
        startIdleReaper(request.optInt("idleCloseS", 0))
        synchronized(lastUsed) { lastUsed[key] = System.nanoTime() }
        synchronized(lockFor(key)) {
            val existing = synchronized(sessions) { sessions[key] }
            if (existing != null && existing.isConnected) return existing
            if (existing != null) {
                synchronized(sessions) { sessions.remove(key) }
                try {
                    existing.disconnect()
                } catch (e: Throwable) {
                    // gia morta: non c'e niente da salvare
                }
            }
            val fresh = openSession(request)
            synchronized(sessions) { sessions[key] = fresh }
            return fresh
        }
    }

    /**
     * Chiude le sessioni ferme da piu di `idleCloseS` secondi.
     *
     * Serve un thread e non un controllo pigro alla chiamata successiva, perche
     * il caso da coprire e proprio quello in cui NESSUNO chiama piu: con
     * `serverAliveInterval` attivo una sessione inutilizzata continuerebbe a
     * pingare il server per sempre dopo un singolo comando, e questo e un
     * telefono — sono batteria e traffico dati. Un solo thread daemon per
     * processo, avviato alla prima connessione e mai fermato: dorme e basta.
     */
    private fun startIdleReaper(idleCloseS: Int) {
        if (idleCloseS <= 0) return
        synchronized(this) {
            if (reaper != null) return
            val tickMs = minOf(idleCloseS.toLong(), 60L) * 1000L
            reaper = Thread {
                while (true) {
                    try {
                        Thread.sleep(tickMs)
                        val cutoff = System.nanoTime() - idleCloseS * 1_000_000_000L
                        val stale = synchronized(lastUsed) {
                            lastUsed.filterValues { it <= cutoff }.keys.toList()
                        }
                        for (key in stale) {
                            synchronized(lastUsed) { lastUsed.remove(key) }
                            val session = synchronized(sessions) { sessions.remove(key) }
                            try {
                                session?.disconnect()
                            } catch (e: Throwable) {
                                // gia morta
                            }
                            Log.i(TAG, "closed idle ssh session")
                        }
                    } catch (e: InterruptedException) {
                        return@Thread
                    } catch (e: Throwable) {
                        // Il potatore non deve mai morire: al giro dopo riprova.
                        Log.w(TAG, "idle reaper: ${e.javaClass.simpleName}")
                    }
                }
            }.apply {
                isDaemon = true
                name = "ssh-idle-reaper"
                start()
            }
        }
    }

    /**
     * UserInfo muto: jsch chiede conferme (nuova host key, passphrase) tramite
     * questa interfaccia e senza un'implementazione userebbe lo standard input,
     * che qui non esiste. Rispondere sempre "no" fa fallire in modo pulito.
     *
     * Resta muto anche per gli host a password, e non e una svista: la password
     * arriva da `session.setPassword`, che jsch usa per il PRIMO tentativo.
     * Questi metodi servono ai RItentativi — cioe al caso "password sbagliata",
     * in cui l'unica risposta giusta e fallire subito con un errore di auth
     * invece di ritentare la stessa credenziale altre due volte.
     */
    private object SilentUserInfo : UserInfo {
        override fun getPassphrase(): String? = null
        override fun getPassword(): String? = null
        override fun promptPassword(message: String?): Boolean = false
        override fun promptPassphrase(message: String?): Boolean = false
        override fun promptYesNo(message: String?): Boolean = false
        override fun showMessage(message: String?) {}
    }

    /**
     * [UserInfo] che sa rispondere anche a *keyboard-interactive*.
     *
     * Serve per parita col backend dev: asyncssh negozia kbdint da solo, jsch
     * no. Senza questo, un server configurato con `PasswordAuthentication no` e
     * `KbdInteractiveAuthentication yes` — cioe il tipico Linux che autentica
     * via PAM — funzionerebbe sul Mac nei test e fallirebbe sul telefono, che e
     * il modo peggiore di scoprire una differenza.
     *
     * Risponde SOLO alla forma inequivocabile: un unico prompt, non in eco (il
     * classico "Password:"). Due prompt, o uno in eco, significano altro — un
     * codice 2FA, una domanda di sicurezza — e li rispondere con la password
     * vorrebbe dire consegnarla a un campo che non e il suo, per giunta a un
     * server che potrebbe averla chiesta apposta. In quei casi si restituisce
     * null e l'autenticazione fallisce in modo pulito.
     */
    private class CredentialUserInfo(private val secret: String?) : UserInfo, UIKeyboardInteractive {
        override fun getPassphrase(): String? = null
        override fun getPassword(): String? = secret
        override fun promptPassword(message: String?): Boolean = secret != null
        override fun promptPassphrase(message: String?): Boolean = false
        override fun promptYesNo(message: String?): Boolean = false
        override fun showMessage(message: String?) {}

        override fun promptKeyboardInteractive(
            destination: String?,
            name: String?,
            instruction: String?,
            prompt: Array<out String>?,
            echo: BooleanArray?,
        ): Array<String>? {
            if (secret == null || prompt == null || prompt.size != 1) return null
            if (echo != null && echo.isNotEmpty() && echo[0]) return null
            return arrayOf(secret)
        }
    }

    // ---- comandi -----------------------------------------------------------

    /**
     * Esegue un comando e ne raccoglie l'output troncato.
     *
     * Un exit code diverso da zero e un RISULTATO, non un errore: l'agente deve
     * poterlo leggere insieme a stderr e decidere. Solleva (cioe risponde con
     * `error`) solo se la connessione o il canale falliscono.
     */
    @JvmStatic
    fun exec(request: String): String = respond {
        val req = parseRequest(request)
        val timeoutMs = (req.getDouble("timeoutS") * 1000).toLong()
        val maxChars = req.getInt("maxOutputChars")
        val session = sessionFor(req)

        val channel = session.openChannel("exec") as ChannelExec
        try {
            channel.setCommand(req.getString("command"))
            // Niente stdin: un comando che aspetta input deve vedere EOF e
            // morire, non restare appeso fino al timeout.
            channel.setInputStream(null)
            val out = StreamCollector(channel.inputStream, maxChars)
            val err = StreamCollector(channel.errStream, maxChars)
            channel.connect((req.optDouble("connectTimeoutS", 15.0) * 1000).toInt())
            out.start()
            err.start()

            val deadline = System.currentTimeMillis() + timeoutMs
            out.join(maxOf(1L, deadline - System.currentTimeMillis()))
            err.join(maxOf(1L, deadline - System.currentTimeMillis()))
            while (!channel.isClosed && System.currentTimeMillis() < deadline) {
                Thread.sleep(20)
            }
            if (!channel.isClosed) {
                throw BridgeException("timeout", "command timed out after ${timeoutMs}ms")
            }

            JSONObject()
                // exitStatus e -1 quando il comando e stato ucciso da un
                // segnale: lo si passa cosi com'e, come fa il backend dev.
                .put("exitCode", channel.exitStatus)
                .put("stdout", out.text.toString())
                .put("stderr", err.text.toString())
                .put("truncatedChars", out.discarded + err.discarded)
        } finally {
            try {
                channel.disconnect()
            } catch (e: Throwable) {
                // canale gia chiuso
            }
        }
    }

    // ---- trasferimenti -----------------------------------------------------

    private inline fun <T> withSftp(req: JSONObject, body: (ChannelSftp) -> T): T {
        val session = sessionFor(req)
        val channel = session.openChannel("sftp") as ChannelSftp
        channel.connect((req.optDouble("connectTimeoutS", 15.0) * 1000).toInt())
        try {
            return body(channel)
        } finally {
            try {
                channel.disconnect()
            } catch (e: Throwable) {
                // canale gia chiuso
            }
        }
    }

    /** Carica un file locale via SFTP. Ritorna i byte trasferiti. */
    @JvmStatic
    fun put(request: String): String = respond {
        val req = parseRequest(request)
        val local = File(req.getString("localPath"))
        if (!local.isFile) {
            throw BridgeException("io", "${local.path} is not a readable file")
        }
        withSftp(req) { sftp -> sftp.put(local.path, req.getString("remotePath")) }
        JSONObject().put("bytes", local.length())
    }

    /**
     * Scarica un file remoto via SFTP.
     *
     * La dimensione si verifica PRIMA di iniziare: un cap applicato mentre si
     * scrive lascerebbe sul telefono un file troncato a meta, indistinguibile
     * da uno buono.
     */
    @JvmStatic
    fun get(request: String): String = respond {
        val req = parseRequest(request)
        val remote = req.getString("remotePath")
        val local = req.getString("localPath")
        val maxBytes = req.getLong("maxBytes")
        val written = withSftp(req) { sftp ->
            val size = sftp.stat(remote).size
            if (size > maxBytes) {
                throw BridgeException("io", "$remote is $size bytes, over the $maxBytes byte limit")
            }
            sftp.get(remote, local)
            size
        }
        JSONObject().put("bytes", written)
    }

    // ---- chiavi e host key -------------------------------------------------

    /**
     * Genera una coppia ed25519 e ritorna SOLO la chiave pubblica.
     *
     * La privata viene scritta su un temporaneo, portata a 0600 e solo dopo
     * rinominata: fra la scrittura e la chmod il file esisterebbe con i permessi
     * di default, che su un path condiviso e una finestra di lettura per chiunque.
     */
    @JvmStatic
    fun generateKeyPair(request: String): String = respond {
        val req = parseRequest(request)
        val target = File(req.getString("keyPath"))
        target.parentFile?.mkdirs()
        val tmp = File(target.parentFile, "${target.name}.tmp")

        val jsch = JSch()
        val pair = KeyPair.genKeyPair(jsch, KeyPair.ED25519)
        try {
            tmp.outputStream().use { pair.writePrivateKey(it) }
            Os.chmod(tmp.path, "600".toInt(8))
            if (!tmp.renameTo(target)) {
                throw BridgeException("io", "could not move the generated key into place")
            }
            val public = ByteArrayOutputStream()
            pair.writePublicKey(public, req.optString("comment", "jenny"))
            JSONObject().put("publicKey", public.toString("UTF-8").trim())
        } finally {
            pair.dispose()
            if (tmp.exists()) tmp.delete()
        }
    }

    /**
     * Legge la host key senza autenticarsi ne eseguire nulla.
     *
     * L'autenticazione fallisce di proposito (utente fittizio, nessuna chiave):
     * la host key viene catturata durante lo scambio di chiavi, che avviene
     * prima. Il fallimento successivo non e un errore da riportare.
     */
    @JvmStatic
    fun probeHostKey(request: String): String = respond {
        val req = parseRequest(request)
        val host = req.getString("host")
        val port = req.getInt("port")

        val captor = CapturingHostKeys()
        val jsch = JSch()
        jsch.hostKeyRepository = captor
        val session = jsch.getSession("jenny-probe", host, port)
        session.userInfo = SilentUserInfo
        session.setConfig("PreferredAuthentications", "publickey")
        try {
            session.connect((req.optDouble("connectTimeoutS", 15.0) * 1000).toInt())
        } catch (e: Throwable) {
            // Auth fallita ad arte: rilanciamo solo se non abbiamo la chiave.
            if (captor.captured == null) throw e
        } finally {
            try {
                session.disconnect()
            } catch (e: Throwable) {
                // gia chiusa
            }
        }
        val blob = captor.captured
            ?: throw BridgeException("io", "$host:$port offered no host key")

        JSONObject()
            .put("line", "${knownHostsName(host, port)} ${keyTypeOf(blob)} ${base64(blob)}")
            .put("fingerprint", "SHA256:" + base64(MessageDigest.getInstance("SHA-256").digest(blob)))
    }

    /**
     * Repository che accetta qualunque host key e la registra.
     *
     * Usato SOLO da [probeHostKey], che per definizione parla con un host non
     * ancora pinnato. Le connessioni vere usano il known_hosts su disco con
     * StrictHostKeyChecking attivo.
     */
    private class CapturingHostKeys : HostKeyRepository {
        var captured: ByteArray? = null
            private set

        override fun check(host: String?, key: ByteArray?): Int {
            if (key != null) captured = key
            return HostKeyRepository.OK
        }

        override fun add(hostkey: HostKey?, ui: UserInfo?) {}
        override fun remove(host: String?, type: String?) {}
        override fun remove(host: String?, type: String?, key: ByteArray?) {}
        override fun getKnownHostsRepositoryID(): String = "jenny-probe"
        override fun getHostKey(): Array<HostKey> = emptyArray()
        override fun getHostKey(host: String?, type: String?): Array<HostKey> = emptyArray()
    }

    /**
     * Tipo di chiave letto dal blob SSH (stringa lunghezza-prefissata iniziale).
     * Si parsa a mano invece di chiederlo a jsch: e il formato del file su disco
     * e non deve dipendere da come una versione di jsch decide di chiamarlo.
     */
    private fun keyTypeOf(blob: ByteArray): String {
        if (blob.size < 4) throw BridgeException("io", "malformed host key")
        val length = ((blob[0].toInt() and 0xff) shl 24) or
            ((blob[1].toInt() and 0xff) shl 16) or
            ((blob[2].toInt() and 0xff) shl 8) or
            (blob[3].toInt() and 0xff)
        if (length <= 0 || length > 64 || 4 + length > blob.size) {
            throw BridgeException("io", "malformed host key")
        }
        return String(blob, 4, length, Charsets.US_ASCII)
    }

    private fun base64(data: ByteArray): String =
        Base64.encodeToString(data, Base64.NO_WRAP or Base64.NO_PADDING)

    /**
     * Nome host come va scritto in known_hosts: una porta non standard cambia il
     * formato in `[host]:port`. Gemello di `known_hosts_name` in
     * `ssh_backends/base.py` — i due lati scrivono lo stesso file.
     */
    private fun knownHostsName(host: String, port: Int): String =
        if (port == 22) host else "[$host]:$port"

    // ---- shutdown ----------------------------------------------------------

    /**
     * Chiude ogni sessione del pool. Il parametro esiste solo per avere una sola
     * convenzione di chiamata (JSON in, JSON out) su tutto il bridge; il
     * contenuto non viene letto.
     */
    @JvmStatic
    fun closeAll(request: String): String = respond {
        val open = synchronized(sessions) {
            val copy = sessions.values.toList()
            sessions.clear()
            copy
        }
        for (session in open) {
            try {
                session.disconnect()
            } catch (e: Throwable) {
                // best-effort: stiamo chiudendo
            }
        }
        JSONObject().put("closed", open.size)
    }
}
