package com.flagdizero.jenny

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Aggiornamento dell'APK dall'interno dell'app: scarico, verifica, installazione.
 * Esposto a Python via Chaquopy (`jclass("com.flagdizero.jenny.UpdateBridge")`),
 * mai istanziato da Kotlin — stesso pattern di NotifierBridge.
 *
 * I metodi sono BLOCCANTI e non lanciano mai verso Python: il lato Python li
 * chiama da un thread e legge il valore di ritorno, che in caso di guaio è una
 * stringa `error:<causa>`. Un'eccezione che attraversa il confine Chaquopy
 * diventa una `JavaException` con uno stack trace Java dentro un traceback
 * Python, cioè la forma meno leggibile possibile di "non ha funzionato".
 *
 * ## Cosa protegge cosa
 *
 * Lo `sha256` passato a [downloadApk] protegge da un download **corrotto o
 * sostituito in transito**: dice che i byte arrivati sono quelli che il canale
 * di distribuzione ha annunciato. Non è, e non va raccontata come, la difesa
 * principale contro un APK ostile — chi controlla la fonte del manifest
 * controlla anche l'hash che ci scrive dentro. La garanzia forte è la **firma
 * dell'APK**: l'aggiornamento è in-place sullo stesso `applicationId`, e il
 * sistema rifiuta l'installazione se il certificato non combacia con quello del
 * pacchetto già installato (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`). Un APK
 * ostile firmato con un'altra chiave non entra, con o senza hash corretto; è
 * anche il motivo per cui i dati dell'utente (workspace, chiavi API, memoria)
 * sopravvivono all'update.
 *
 * ## Perché il fallback non è un ramo d'emergenza
 *
 * L'installazione silenziosa (`setRequireUserAction(USER_ACTION_NOT_REQUIRED)`)
 * è una concessione del sistema, non un diritto dell'app, e qui non abbiamo la
 * carta migliore per ottenerla: l'APK sul telefono di riferimento è installato
 * via `adb`, quindi l'installer of record non siamo noi. La regola AOSP su cui
 * si appoggia questo codice è l'altro ramo, quello di *self-update* — l'uid
 * dell'installer coincide con l'uid del pacchetto da aggiornare — che non
 * richiede di essere installer of record. Ma Android 14 ha introdotto l'*update
 * ownership* e le versioni successive hanno stretto ancora, e il dispositivo di
 * riferimento è un Unihertz Titan 2 su Android 16 con ROM cinese: l'esito reale
 * si conosce solo al `commit`. Per questo il percorso con conferma dell'utente è
 * trattato come la strada normale, completo di notifica per il caso headless
 * (v. [surfaceConfirmation]), e non come un `else` scritto per scrupolo.
 *
 * Dopo un update riuscito il sistema uccide il processo e NON lo fa ripartire:
 * a rimettere in piedi il gateway ci pensa `BootReceiver` su
 * `MY_PACKAGE_REPLACED` (v. AndroidManifest.xml). Qui non serve fare nulla.
 */
class UpdateBridge(context: Context) {

    companion object {
        private const val TAG = "UpdateBridge"

        /** Sottocartella di `cacheDir`. In `cacheDir` e non in `filesDir` perché
         *  un APK da centinaia di MB è esattamente il tipo di file che il
         *  sistema deve poter buttare via quando lo spazio finisce, e perché
         *  non ha senso includerlo nei backup. */
        private const val UPDATES_DIR = "updates"
        private const val APK_NAME = "jenny-update.apk"

        private const val CONNECT_TIMEOUT_MS = 30_000
        private const val READ_TIMEOUT_MS = 60_000
        private const val MAX_REDIRECTS = 5
        private const val BUFFER_SIZE = 64 * 1024

        /** Margine oltre allo spazio strettamente necessario: sotto questa
         *  soglia il telefono è comunque in guaio e conviene fermarsi prima di
         *  aver scritto mezzo APK. */
        private const val FREE_SPACE_MARGIN_BYTES = 64L * 1024 * 1024

        /** Quanto si aspetta il primo esito dopo il `commit`.
         *
         *  Generoso apposta: fra il commit e la decisione del sistema ci sono la
         *  copia dell'APK nell'area di staging e la verifica (Play Protect o
         *  l'equivalente della ROM), che su un APK grosso e un telefono lento
         *  non sono istantanee. Scaduto il tempo senza alcuno stato, l'install è
         *  comunque in volo: v. il valore di ritorno di [installApk]. */
        private const val COMMIT_TIMEOUT_MS = 120_000L

        /** Action della broadcast di stato. Il `PendingIntent` la porta con sé
         *  ed è l'unico modo per collegare un esito alla sessione che l'ha
         *  prodotto. */
        private const val ACTION_INSTALL_STATUS = "com.flagdizero.jenny.INSTALL_STATUS"

        private const val UPDATE_CHANNEL_ID = "jenny_updates"
        private const val UPDATE_NOTIFICATION_ID = 3

        /**
         * Stato che deve sopravvivere all'istanza, per lo stesso motivo per cui
         * i wakelock di `PowerBridge` vivono nel companion: Chaquopy costruisce
         * un bridge nuovo a ogni `jclass(...)(context)`, e il lato Python lo
         * ricrea dopo ogni `reset_install_state()`. Un ritentativo che arriva su
         * un'istanza fresca deve poter riconoscere l'APK già in cache e la
         * conferma già mostrata, altrimenti "ho ripremuto il pulsante" torna a
         * significare "riscarica tutto".
         *
         * `@Volatile` e non `synchronized`: sono due riferimenti immutabili
         * scritti da un thread di lavoro Python e letti dal successivo, e il
         * lato Python già serializza le installazioni una alla volta.
         */
        @Volatile
        private var verifiedApkHash: String? = null

        @Volatile
        private var pendingConfirmation: PendingConfirmation? = null
    }

    /**
     * Sessione già committata che aspetta soltanto il tocco dell'utente. Non è
     * spazzatura da ripulire: è un'installazione a un passo dalla fine, e
     * l'`intent` è quello che il sistema ci ha dato per ripresentarla.
     */
    private data class PendingConfirmation(
        val sessionId: Int,
        val apkHash: String,
        val intent: Intent,
    )

    private val appContext = context.applicationContext

    // ── Interrogazioni a basso costo ─────────────────────────────────────────

    /** `versionCode` del pacchetto installato, o -1 se illeggibile (non
     *  succede: stiamo interrogando noi stessi). Serve a Python per decidere se
     *  la versione annunciata è davvero più recente. */
    @Suppress("DEPRECATION")
    fun installedVersionCode(): Long {
        return try {
            val info = appContext.packageManager.getPackageInfo(appContext.packageName, 0)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                info.longVersionCode
            } else {
                info.versionCode.toLong()
            }
        } catch (e: Exception) {
            Log.e(TAG, "Cannot read own package info", e)
            -1L
        }
    }

    // ── Download ─────────────────────────────────────────────────────────────

    /**
     * Scarica l'APK in `cacheDir/updates/` verificandone integrità e dimensione.
     * Ritorna il path assoluto del file, oppure `error:<causa>`.
     *
     * Se in cache c'è già un file il cui **sha256 combacia**, quello viene
     * riusato e non si scarica niente. Il caso non è raro, è la norma: dopo un
     * esito `prompt` l'utente non tocca la notifica subito, torna nella WebUI e
     * ripreme "Installa ora" — e senza questo controllo si ributterebbero via
     * decine di MB già scaricati e verificati, potenzialmente su dati mobili. A
     * decidere è sempre l'hash, mai il nome o la data del file: quello che c'è
     * in `cacheDir` può essere l'APK di un'altra versione, o un download
     * interrotto a metà.
     *
     * @param url deve essere https, redirect compresi. Chi chiama l'ha già
     *   validato contro la policy SSRF hop per hop (v.
     *   `update_install._resolve_apk_url`): qui lo schema è l'ultimo pavimento,
     *   non il controllo principale.
     * @param sha256 64 caratteri esadecimali, confronto case-insensitive.
     * @param expectedSize dimensione attesa in byte; serve sia a controllare lo
     *   spazio prima di iniziare sia a rifiutare un file troncato.
     */
    fun downloadApk(url: String, sha256: String, expectedSize: Long): String {
        val expectedHash = sha256.trim()
        if (expectedHash.length != 64 || !expectedHash.all { it.isDigit() || it in 'a'..'f' || it in 'A'..'F' }) {
            return "error:invalid sha256"
        }
        if (expectedSize <= 0) return "error:invalid expected size"

        val parsed = try {
            URL(url)
        } catch (e: Exception) {
            return "error:malformed url"
        }
        if (!parsed.protocol.equals("https", ignoreCase = true)) {
            return "error:url is not https"
        }

        val dir = updatesDir() ?: return "error:cannot create updates dir"
        val target = File(dir, APK_NAME)

        // Riuso della cache. Qui serve spazio solo per la copia che il
        // PackageInstaller farà nella sua area di staging: l'APK è già sul
        // disco e non lo si riscrive.
        if (cachedApkMatches(target, expectedHash, expectedSize)) {
            if (!hasRoomFor(dir, expectedSize + FREE_SPACE_MARGIN_BYTES)) {
                return "error:not enough free space"
            }
            verifiedApkHash = expectedHash
            Log.i(TAG, "Reusing the cached update: ${target.absolutePath} (sha256 ok)")
            return target.absolutePath
        }

        // Da qui in poi si scarica: quello che c'era in cache non è l'APK che
        // ci serve, e l'hash memorizzato non descrive più niente.
        verifiedApkHash = null
        purgeUpdatesDir(dir)

        // Serve il doppio: l'APK scaricato più la copia che il PackageInstaller
        // fa nella sua area di staging al momento del commit. Scoprirlo a metà
        // installazione, con la cache già piena, è il modo silenzioso di fallire
        // su un telefono usato come server — dove nessuno guarda lo spazio
        // libero finché qualcosa non smette di funzionare.
        if (!hasRoomFor(dir, expectedSize * 2 + FREE_SPACE_MARGIN_BYTES)) {
            return "error:not enough free space"
        }

        return try {
            val digest = MessageDigest.getInstance("SHA-256")
            var written = 0L
            val connection = openHttps(parsed)
            try {
                // Se il server dichiara una lunghezza diversa da quella attesa
                // il file è già l'oggetto sbagliato: inutile scaricarlo tutto
                // per poi buttarlo.
                val declared = connection.contentLengthLong
                if (declared > 0 && declared != expectedSize) {
                    throw IOException("Content-Length $declared != expected $expectedSize")
                }
                connection.inputStream.use { input ->
                    FileOutputStream(target).use { output ->
                        val buffer = ByteArray(BUFFER_SIZE)
                        while (true) {
                            val read = input.read(buffer)
                            if (read < 0) break
                            output.write(buffer, 0, read)
                            // Hash calcolato in streaming sugli stessi byte che
                            // finiscono su disco: rileggere il file dopo
                            // sarebbe una seconda passata su centinaia di MB e
                            // lascerebbe una finestra fra scrittura e verifica.
                            digest.update(buffer, 0, read)
                            written += read
                        }
                        output.flush()
                        output.fd.sync()
                    }
                }
            } finally {
                connection.disconnect()
            }

            if (written != expectedSize) {
                target.delete()
                Log.e(TAG, "Size mismatch: got $written, expected $expectedSize")
                return "error:size mismatch"
            }
            val actualHash = digest.digest().toHex()
            if (!actualHash.equals(expectedHash, ignoreCase = true)) {
                target.delete()
                Log.e(TAG, "SHA-256 mismatch: got $actualHash, expected $expectedHash")
                return "error:sha256 mismatch"
            }
            verifiedApkHash = expectedHash
            Log.i(TAG, "Downloaded update: ${target.absolutePath} ($written bytes, sha256 ok)")
            target.absolutePath
        } catch (e: Exception) {
            target.delete()
            Log.e(TAG, "Download failed", e)
            // La causa esatta finisce in logcat e NON nella stringa di ritorno,
            // che risale fino alla chat e alla WebUI. Distinguere
            // `ConnectException` da `SocketTimeoutException` da `HTTP 401`
            // trasforma questo metodo in un oracolo: chi controlla il manifest
            // controlla l'URL, e leggere quale delle tre risposte arriva
            // significa mappare la rete privata del telefono un indirizzo alla
            // volta. La validazione SSRF lato Python chiude la porta principale;
            // questo toglie il premio anche a chi trovasse una fessura (un
            // redirect comparso dopo la nostra risoluzione, un DNS che cambia
            // risposta fra i due lookup). Chi ha il telefono in mano la causa
            // vera ce l'ha comunque, a un `adb logcat` di distanza.
            "error:download failed (see logcat)"
        }
    }

    /** Cartella dei download, creata se manca. Non tocca il contenuto: a
     *  decidere che cosa sopravvive è [downloadApk], che sa quale APK sta
     *  cercando. */
    private fun updatesDir(): File? {
        val dir = File(appContext.cacheDir, UPDATES_DIR)
        if (!dir.isDirectory && !dir.mkdirs()) {
            Log.e(TAG, "Cannot create ${dir.absolutePath}")
            return null
        }
        return dir
    }

    /**
     * Svuota la cartella dei download. Si chiama solo quando si sta per
     * scaricare davvero: a quel punto quello che c'è dentro è per definizione
     * spazzatura — un download interrotto, o l'APK di un'altra versione che
     * nessuno cancellerà mai perché dopo un update il processo viene ucciso.
     */
    private fun purgeUpdatesDir(dir: File) {
        dir.listFiles()?.forEach { stale ->
            if (!stale.delete()) Log.w(TAG, "Could not delete stale file ${stale.name}")
        }
    }

    /**
     * True se [file] è **esattamente** l'APK atteso: dimensione giusta e
     * sha256 giusto, ricalcolato adesso sui byte su disco. Rileggere qualche
     * decina di MB costa meno di un secondo e non si fida di niente che non sia
     * il contenuto — né del nome, né di quello che credevamo di aver scritto la
     * volta scorsa (la cache è di sistema: Android può svuotarla quando vuole).
     */
    private fun cachedApkMatches(file: File, expectedHash: String, expectedSize: Long): Boolean {
        if (!file.isFile || file.length() != expectedSize) return false
        return try {
            val digest = MessageDigest.getInstance("SHA-256")
            FileInputStream(file).use { input ->
                val buffer = ByteArray(BUFFER_SIZE)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    digest.update(buffer, 0, read)
                }
            }
            digest.digest().toHex().equals(expectedHash, ignoreCase = true)
        } catch (e: Exception) {
            Log.w(TAG, "Could not hash the cached APK", e)
            false
        }
    }

    /** True se su quel volume c'è ancora [needed] byte liberi. Un errore nel
     *  leggere lo spazio non ferma l'aggiornamento: meglio provarci. */
    private fun hasRoomFor(dir: File, needed: Long): Boolean {
        val available = try {
            dir.usableSpace
        } catch (e: Exception) {
            Long.MAX_VALUE
        }
        if (available >= needed) return true
        Log.e(TAG, "Not enough space: need $needed bytes, have $available")
        return false
    }

    /**
     * Apre la connessione seguendo i redirect a mano. `HttpURLConnection` li
     * seguirebbe da solo, ma si rifiuta di cambiare protocollo e soprattutto non
     * ci farebbe vedere dove siamo finiti: qui ogni salto viene ricontrollato,
     * così un `302` verso http non può declassare il download a metà strada.
     */
    private fun openHttps(start: URL): HttpURLConnection {
        var current = start
        var hops = 0
        while (true) {
            if (!current.protocol.equals("https", ignoreCase = true)) {
                throw IOException("redirect to non-https URL")
            }
            val connection = (current.openConnection() as HttpURLConnection).apply {
                instanceFollowRedirects = false
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                requestMethod = "GET"
                setRequestProperty("Accept", "application/vnd.android.package-archive, */*")
            }
            val code = connection.responseCode
            if (code in 300..399) {
                val location = connection.getHeaderField("Location")
                connection.disconnect()
                if (location.isNullOrBlank()) throw IOException("redirect without Location")
                if (++hops > MAX_REDIRECTS) throw IOException("too many redirects")
                current = URL(current, location)
                continue
            }
            if (code != HttpURLConnection.HTTP_OK) {
                connection.disconnect()
                throw IOException("HTTP $code")
            }
            return connection
        }
    }

    private fun ByteArray.toHex(): String {
        val out = StringBuilder(size * 2)
        for (b in this) {
            val v = b.toInt() and 0xff
            out.append("0123456789abcdef"[v ushr 4])
            out.append("0123456789abcdef"[v and 0x0f])
        }
        return out.toString()
    }

    // ── Installazione ────────────────────────────────────────────────────────

    /**
     * Installa l'APK all'indirizzo [path] tramite `PackageInstaller`.
     *
     * Ritorna:
     * - `"silent"` — sessione committata senza che il sistema abbia chiesto
     *   niente all'utente. NON vuol dire "installato": la conferma vera è che il
     *   processo venga ucciso e riparta su un `installedVersionCode()` più alto.
     *   Nel caso silenzioso questo metodo, molto probabilmente, non fa nemmeno in
     *   tempo a ritornare — il processo muore mentre aspetta l'esito.
     * - `"prompt"` — il sistema ha rifiutato l'update non presidiato e ha
     *   restituito l'Intent dell'installer, che è stato mostrato all'utente. Da
     *   qui in poi decide lui.
     * - `"error:<causa>"` — sessione fallita, o l'APK non è installabile.
     *
     * Se una conferma per **questo stesso APK** è già stata mostrata e nessuno
     * l'ha ancora toccata, viene semplicemente ripresentata: v.
     * [resurfacePendingConfirmation].
     */
    fun installApk(path: String): String {
        val apk = File(path)
        if (!apk.isFile) return "error:apk not found"
        val length = apk.length()
        if (length <= 0) return "error:apk is empty"

        val installer = appContext.packageManager.packageInstaller
        resurfacePendingConfirmation(installer)?.let { return it }
        abandonStaleSessions(installer)

        var session: PackageInstaller.Session? = null
        return try {
            val params = PackageInstaller.SessionParams(
                PackageInstaller.SessionParams.MODE_FULL_INSTALL
            ).apply {
                // Vincola la sessione al NOSTRO pacchetto: se l'APK scaricato
                // dichiarasse un applicationId diverso la sessione fallisce
                // invece di installare, sotto la nostra identità, qualcos'altro.
                setAppPackageName(appContext.packageName)
                // Suggerimento al sistema per l'area di staging: gli permette di
                // fare spazio prima invece di fallire a copia iniziata.
                try {
                    setSize(length)
                } catch (e: Exception) {
                    Log.w(TAG, "setSize rejected", e)
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    // È una *richiesta*. Il sistema può ignorarla e rispondere
                    // con STATUS_PENDING_USER_ACTION; alcune ROM la rifiutano
                    // direttamente lanciando, e non è un motivo per non provare.
                    try {
                        setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
                    } catch (e: Throwable) {
                        Log.w(TAG, "setRequireUserAction not accepted by this ROM", e)
                    }
                }
            }

            val sessionId = installer.createSession(params)
            session = installer.openSession(sessionId)
            session.openWrite(APK_NAME, 0, length).use { output ->
                FileInputStream(apk).use { input ->
                    input.copyTo(output, BUFFER_SIZE)
                }
                // Dentro lo `use`: dopo la chiusura dello stream la sessione non
                // accetta più fsync, e senza fsync il commit può trovarsi byte
                // non ancora arrivati sullo storage.
                session.fsync(output)
            }

            val outcome = commitAndAwait(session, sessionId)
            // Da qui in poi la sessione appartiene al sistema: un `abandon` nel
            // finally annullerebbe un'installazione già avviata.
            session = null
            outcome
        } catch (e: Exception) {
            Log.e(TAG, "Install failed", e)
            "error:install failed (${e.javaClass.simpleName}: ${e.message})"
        } finally {
            session?.let {
                try {
                    it.abandon()
                } catch (e: Exception) {
                    Log.w(TAG, "Abandon failed", e)
                }
                it.close()
            }
        }
    }

    /**
     * Committa la sessione e aspetta il primo stato.
     *
     * Il receiver è **registrato a runtime**, non nel manifest, per due motivi.
     * Il primo è la durata: serve per il tempo di questa chiamata e non un
     * minuto di più, e `RECEIVER_NOT_EXPORTED` con la broadcast consegnata dal
     * nostro stesso `PendingIntent` significa che nessun'altra app può
     * fabbricare un esito falso. Il secondo è il caso che un receiver da
     * manifest coprirebbe in più — l'esito che arriva quando il nostro processo
     * è già morto — ed è esattamente quello per cui NON vogliamo essere
     * risvegliati: un update riuscito uccide il processo, e farlo ripartire da
     * zero (JennyApplication, Chaquopy, il gateway) solo per scrivere una riga
     * di log sarebbe uno spreco, oltre che una corsa con
     * `BootReceiver`/`MY_PACKAGE_REPLACED`, che quel riavvio lo fa già come si
     * deve.
     */
    private fun commitAndAwait(session: PackageInstaller.Session, sessionId: Int): String {
        val statuses = ArrayBlockingQueue<Intent>(1)
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                if (intent == null) return
                if (intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1) != sessionId) return
                statuses.offer(intent)
            }
        }
        ContextCompat.registerReceiver(
            appContext,
            receiver,
            IntentFilter(ACTION_INSTALL_STATUS),
            ContextCompat.RECEIVER_NOT_EXPORTED
        )
        try {
            // MUTABLE non è negoziabile: è il sistema a riempire l'intent con lo
            // stato e con l'Intent di conferma.
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
            val pending = PendingIntent.getBroadcast(
                appContext,
                sessionId,
                Intent(ACTION_INSTALL_STATUS).setPackage(appContext.packageName),
                flags
            )
            session.commit(pending.intentSender)
            session.close()

            val status = statuses.poll(COMMIT_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            if (status == null) {
                // Nessun esito entro la finestra e siamo ancora vivi. L'install
                // è comunque in corso e nessuno ci ha chiesto niente: è la
                // definizione di "silent" del contratto (committata senza
                // interazione), non una promessa che sia andata a buon fine.
                Log.w(TAG, "Session $sessionId: no status within ${COMMIT_TIMEOUT_MS}ms, install still in flight")
                return "silent"
            }
            val code = status.getIntExtra(PackageInstaller.EXTRA_STATUS, Int.MIN_VALUE)
            val message = status.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
            return when (code) {
                PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                    Log.i(TAG, "Session $sessionId: system requires user confirmation")
                    val confirm = status.confirmationIntent()
                    if (confirm == null) {
                        Log.e(TAG, "Session $sessionId: PENDING_USER_ACTION without EXTRA_INTENT")
                        "error:missing confirmation intent"
                    } else if (surfaceConfirmation(confirm)) {
                        rememberPendingConfirmation(sessionId, confirm)
                        "prompt"
                    } else {
                        "error:cannot show installer"
                    }
                }
                PackageInstaller.STATUS_SUCCESS -> {
                    Log.i(TAG, "Session $sessionId: installed silently")
                    "silent"
                }
                else -> {
                    Log.e(TAG, "Session $sessionId failed: status=$code message=$message")
                    "error:install status $code${if (message != null) " ($message)" else ""}"
                }
            }
        } finally {
            try {
                appContext.unregisterReceiver(receiver)
            } catch (e: Exception) {
                Log.w(TAG, "Receiver already unregistered", e)
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun Intent.confirmationIntent(): Intent? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
        } else {
            getParcelableExtra(Intent.EXTRA_INTENT) as? Intent
        }

    /**
     * Mostra all'utente la conferma dell'installer di sistema. Ritorna false
     * solo se non si è riusciti né a lanciarla né ad annunciarla.
     *
     * Il `startActivity` diretto funziona **solo con l'app in primo piano**: da
     * Android 10 un avvio di activity dal background viene scartato in silenzio,
     * e "in silenzio" qui vuol dire un aggiornamento che resta fermo per sempre
     * senza che nessuno se ne accorga — che è lo scenario normale per questo
     * telefono, un server in un cassetto con lo schermo spento. Perciò fuori dal
     * foreground si passa da una notifica, che l'utente trova quando riprende in
     * mano il telefono: il `PendingIntent` conserva l'Intent di conferma e il tap
     * lo lancia con l'app in primo piano, dove nessuno lo blocca.
     */
    private fun surfaceConfirmation(confirm: Intent): Boolean {
        confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (MainActivity.isInForeground) {
            try {
                appContext.startActivity(confirm)
                Log.i(TAG, "System installer launched (app in foreground)")
                return true
            } catch (e: Exception) {
                Log.w(TAG, "startActivity for installer failed, falling back to notification", e)
            }
        }
        return postConfirmationNotification(confirm)
    }

    private fun postConfirmationNotification(confirm: Intent): Boolean {
        return try {
            val manager = appContext.getSystemService(NotificationManager::class.java)
                ?: return false
            // Canale dedicato e non `jenny_alerts`: quella è la voce dell'agente
            // e l'utente deve poterla silenziare senza perdersi il fatto che un
            // aggiornamento è fermo in attesa di un suo tocco.
            manager.createNotificationChannel(
                NotificationChannel(
                    UPDATE_CHANNEL_ID,
                    appContext.getString(R.string.update_channel_name),
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = appContext.getString(R.string.update_channel_description)
                }
            )
            val pending = PendingIntent.getActivity(
                appContext,
                UPDATE_NOTIFICATION_ID,
                confirm,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            val notification = NotificationCompat.Builder(appContext, UPDATE_CHANNEL_ID)
                .setContentTitle(appContext.getString(R.string.update_notification_title))
                .setContentText(appContext.getString(R.string.update_notification_text))
                .setSmallIcon(R.drawable.ic_stat_jenny)
                .setAutoCancel(true)
                .setOngoing(false)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_RECOMMENDATION)
                .setContentIntent(pending)
                .build()
            manager.notify(UPDATE_NOTIFICATION_ID, notification)
            Log.i(TAG, "Install confirmation posted as a notification (app not in foreground)")
            true
        } catch (e: Exception) {
            // Comprende la SecurityException su API 33+ senza POST_NOTIFICATIONS.
            Log.e(TAG, "Cannot surface install confirmation", e)
            false
        }
    }

    /**
     * Registra la conferma appena mostrata, così un secondo tentativo possa
     * ripresentarla invece di rifare tutto. La si lega all'hash dell'APK e non
     * al path (che è sempre lo stesso file): è l'unica cosa che distingue la
     * versione per cui l'utente sta per toccare Installa da un'altra.
     *
     * Senza hash verificato in questo processo — `installApk` chiamato su un
     * path che non abbiamo scaricato noi — non si registra niente: meglio un
     * ritentativo che rifà la sessione che uno che ne ripresenta una sbagliata.
     */
    private fun rememberPendingConfirmation(sessionId: Int, confirm: Intent) {
        val hash = verifiedApkHash
        if (hash == null) {
            Log.w(TAG, "Session $sessionId: pending confirmation not tracked (unknown APK hash)")
            return
        }
        pendingConfirmation = PendingConfirmation(sessionId, hash, confirm)
    }

    /**
     * Se c'è già una conferma in sospeso per lo stesso APK, la rimostra e
     * ritorna `"prompt"`; altrimenti `null` e si procede normalmente.
     *
     * Il caso è quello di sempre su questo telefono: sessione committata,
     * notifica postata, l'utente non la tocca, torna nella WebUI e ripreme
     * "Installa ora". Rifare la sessione da capo vorrebbe dire una seconda
     * copia dell'APK nell'area di staging e una seconda notifica per la stessa
     * identica installazione — mentre quella vecchia era già a un tocco dalla
     * fine.
     *
     * Tre condizioni, e servono tutte e tre: l'APK in cache deve essere quello
     * della conferma (altrimenti si installerebbe una versione superata), la
     * sessione deve esistere ancora (l'utente può averla annullata, o il
     * sistema averla fatta scadere) e la conferma deve riuscire a comparire. Se
     * la sessione non c'è più — o se è di un APK ormai vecchio — la si
     * dimentica: nel secondo caso abbandonandola, perché la sua copia in
     * staging è spazio occupato per un'installazione che nessuno vuole più.
     */
    private fun resurfacePendingConfirmation(installer: PackageInstaller): String? {
        val waiting = pendingConfirmation ?: return null
        val alive = try {
            installer.getSessionInfo(waiting.sessionId) != null
        } catch (e: Exception) {
            Log.w(TAG, "Cannot read session ${waiting.sessionId}", e)
            false
        }
        if (!alive) {
            Log.i(TAG, "Pending session ${waiting.sessionId} is gone, starting a new one")
            pendingConfirmation = null
            return null
        }
        if (waiting.apkHash != verifiedApkHash) {
            Log.i(TAG, "Pending session ${waiting.sessionId} is for another APK, abandoning it")
            pendingConfirmation = null
            try {
                installer.abandonSession(waiting.sessionId)
            } catch (e: Exception) {
                Log.w(TAG, "Cannot abandon superseded session ${waiting.sessionId}", e)
            }
            return null
        }
        // Copia difensiva: `surfaceConfirmation` aggiunge flag all'Intent, e
        // quello memorizzato deve restare riutilizzabile una terza volta.
        if (!surfaceConfirmation(Intent(waiting.intent))) {
            Log.w(TAG, "Could not re-surface session ${waiting.sessionId}, starting a new one")
            return null
        }
        Log.i(TAG, "Re-surfaced the confirmation for session ${waiting.sessionId}")
        return "prompt"
    }

    /**
     * Abbandona le sessioni rimaste appese da tentativi precedenti. Ognuna si
     * porta dietro la sua copia dell'APK nell'area di staging: lasciarle lì
     * significa consumare spazio per un'installazione che nessuno completerà
     * più. Una sessione ancora `isActive` è una sessione che qualcuno sta
     * scrivendo adesso e non si tocca.
     *
     * Nemmeno una sessione **già committata** si tocca, ed è la differenza che
     * conta: dal punto di vista di questo metodo è indistinguibile da una
     * morta — nessuno la sta scrivendo — ma è una installazione che il sistema
     * ha in carico e che, nel caso normale su questo telefono, sta aspettando
     * solo che l'utente tocchi Installa. Abbandonarla vuol dire cancellare la
     * conferma che gli abbiamo appena messo in notifica. Non è nemmeno un
     * leak: le sessioni committate le porta a termine o le fa scadere il
     * sistema, non noi.
     */
    private fun abandonStaleSessions(installer: PackageInstaller) {
        try {
            installer.mySessions.forEach { info ->
                if (info.isActive) return@forEach
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && info.isCommitted) {
                    return@forEach
                }
                try {
                    installer.abandonSession(info.sessionId)
                    Log.i(TAG, "Abandoned stale session ${info.sessionId}")
                } catch (e: Exception) {
                    Log.w(TAG, "Cannot abandon session ${info.sessionId}", e)
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Cannot enumerate installer sessions", e)
        }
    }
}
