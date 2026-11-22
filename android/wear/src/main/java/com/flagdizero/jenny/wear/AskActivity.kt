package com.flagdizero.jenny.wear

import android.Manifest
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.AlarmClock
import android.speech.RecognitionListener
import android.speech.RecognitionService
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.text.InputType
import android.util.Log
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.FrameLayout
import com.flagdizero.jenny.wearprotocol.WearProtocol
import com.google.android.gms.wearable.CapabilityClient
import com.google.android.gms.wearable.MessageClient
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import java.lang.ref.WeakReference
import java.util.UUID

/**
 * Jenny sul polso, primo passo: manda una domanda, ricevi la risposta.
 *
 * Una schermata sola ([PolsoView]): Jenny intera che si tocca per parlare,
 * *Ti ascolto* mentre si parla, *Ci penso* mentre Jenny lavora sul telefono,
 * poi la risposta. Si apre dall'elenco app:
 * il tasto laterale (il ruolo di assistente) è il secondo passo, quando questo
 * filo avrà dimostrato di reggere.
 *
 * Il giro con il telefono è in [WearProtocol]. Le regole che contano qui:
 *
 * * **ogni domanda ha un id**, e si mostra solo la risposta che porta quello
 *   della domanda in corso — una risposta in ritardo a una domanda vecchia non
 *   copre quella nuova;
 * * **una domanda non sparisce in silenzio**: se il telefono non conferma entro
 *   [WearProtocol.ACK_TIMEOUT_MS] compare "Jenny non raggiungibile", e se la
 *   risposta non arriva entro [WearProtocol.REPLY_TIMEOUT_MS] lo si dice —
 *   la risposta resta comunque nella chat sul telefono.
 *
 * Le risposte si ascoltano finché l'activity esiste, anche a schermo spento:
 * il listener si toglie in `onDestroy`, non in `onPause`. Con l'app chiusa la
 * risposta arriva solo sul telefono, ed è un limite accettato di questo passo.
 */
class AskActivity : Activity(), MessageClient.OnMessageReceivedListener {

    private lateinit var polso: PolsoView
    private val main = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var voiceOn = false
    private val ownVoice by lazy { OwnVoice(this, main) }

    /** Il campo che non si vede, dove scrive la tastiera: nasce al primo uso. */
    private var typed: EditText? = null

    /** Il riconoscitore senza microfono la cui schermata è aperta, se lo è. */
    private var micGap: MicGap? = null

    /** L'id della domanda in attesa, o `null` se non si aspetta niente. */
    private var pendingId: String? = null

    /** L'ultima domanda mandata: un errore la fa riprovare toccando Jenny. */
    private var lastQuestion: String? = null

    private val ackTimeout = Runnable { fail(R.string.unreachable, "ack timeout") }
    private val replyTimeout = Runnable { fail(R.string.too_slow, "reply timeout") }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // La voce resta com'era l'ultima volta: accesa o spenta la si sceglie
        // una volta, non a ogni apertura.
        voiceOn = prefs().getBoolean(PREF_VOICE_ON, false)
        if (voiceOn) ensureTts()
        polso = PolsoView(this, voiceOn)
        setContentView(polso)
        polso.setOnParla { listen() }
        polso.setOnScrivi { type() }
        polso.setOnToggle { on ->
            voiceOn = on
            prefs().edit().putBoolean(PREF_VOICE_ON, on).apply()
            if (on) ensureTts() else hush()
        }
        Wearable.getMessageClient(this).addListener(this)
        active = WeakReference(this)
        listenIfAsked(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        listenIfAsked(intent)
    }

    /** Aperta dal tasto dell'assistente: si ascolta subito, senza toccare niente. */
    private fun listenIfAsked(intent: Intent?) {
        val asked = intent?.getBooleanExtra(EXTRA_LISTEN, false) == true ||
            intent?.action == Intent.ACTION_ASSIST || intent?.action == "android.intent.action.VOICE_ASSIST"
        if (!asked) return
        intent?.removeExtra(EXTRA_LISTEN)
        Log.i(TAG, "Opened as the assistant: listening")
        // Una domanda in attesa non si interrompe: il tasto la lascia finire.
        if (pendingId == null) listen()
    }

    override fun onDestroy() {
        Wearable.getMessageClient(this).removeListener(this)
        main.removeCallbacksAndMessages(null)
        recognizer?.destroy()
        ownVoice.stop()
        tts?.shutdown()
        if (active?.get() === this) active = null
        super.onDestroy()
    }

    // --- Parla ---------------------------------------------------------------

    private fun listen() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_AUDIO)
            return
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            polso.error(getString(R.string.recognizer_error), null)
            return
        }
        recognizerWithoutMic()?.let { gap ->
            Log.i(TAG, "No speech recognizer holds the microphone permission: using ${gap.pkg}'s own screen")
            if (!listenWithSystemScreen(gap)) polso.error(getString(R.string.recognizer_no_mic, gap.label), null)
            return
        }
        hush()
        val rec = recognizer ?: createRecognizer().also { recognizer = it }
        rec.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) = polso.level(rmsdB)
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() = polso.rest()
            override fun onEvent(eventType: Int, params: Bundle?) {}

            override fun onError(error: Int) {
                Log.i(TAG, "Recognizer error $error")
                polso.error(getString(when (error) {
                    SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT ->
                        R.string.heard_nothing
                    SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
                        R.string.no_network
                    else -> R.string.recognizer_error
                }), null)
            }

            override fun onPartialResults(partial: Bundle) {
                firstResult(partial)?.let { polso.listening(it) }
            }

            override fun onResults(results: Bundle) {
                val text = firstResult(results)
                if (text.isNullOrBlank()) polso.error(getString(R.string.heard_nothing), null) else ask(text)
            }
        })
        polso.listening("")
        rec.startListening(
            Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
        )
    }

    /**
     * I riconoscitori vocali dell'orologio, senza quello finto di Jenny
     * ([NoRecognitionService], che esiste solo per il ruolo di assistente).
     */
    private fun systemRecognizers() =
        packageManager.queryIntentServices(Intent(RecognitionService.SERVICE_INTERFACE), 0)
            .map { it.serviceInfo }
            .filter { it.packageName != packageName }

    /**
     * Il riconoscitore predefinito, a meno che il sistema non abbia messo lì
     * quello finto di Jenny: allora si prende il primo vero, per nome.
     */
    private fun createRecognizer(): SpeechRecognizer {
        val default = android.provider.Settings.Secure.getString(contentResolver, "voice_recognition_service")
        val mine = default?.startsWith("$packageName/") == true
        val real = systemRecognizers().firstOrNull()
        return if (mine && real != null) {
            SpeechRecognizer.createSpeechRecognizer(this, ComponentName(real.packageName, real.name))
        } else {
            SpeechRecognizer.createSpeechRecognizer(this)
        }
    }

    /** Un riconoscitore vocale senza microfono: il pacchetto e il nome da mostrare. */
    private data class MicGap(val pkg: String, val label: String)

    /**
     * Il riconoscitore vocale da sistemare, se **nessuno** di quelli
     * dell'orologio ha il permesso del microfono; `null` se almeno uno ce l'ha.
     *
     * Ad ascoltare non è Jenny ma il riconoscitore di sistema, in un altro
     * processo, e il permesso che conta è il suo. Sul OnePlus Watch 3 il
     * servizio vocale di Google arriva senza: il riconoscitore di rete fallisce
     * con `MICROPHONE_UNAVAILABLE`, ripiega su quello offline, che non ha la
     * lingua, e a Jenny arriva un errore 12 che non dice niente del microfono.
     */
    private fun recognizerWithoutMic(): MicGap? {
        val pm = packageManager
        val packages = systemRecognizers().map { it.packageName }.distinct()
        if (packages.isEmpty()) return null
        val anyWithMic = packages.any {
            pm.checkPermission(Manifest.permission.RECORD_AUDIO, it) == PackageManager.PERMISSION_GRANTED
        }
        if (anyWithMic) return null
        val first = packages.first()
        val label = try {
            pm.getApplicationLabel(pm.getApplicationInfo(first, 0)).toString()
        } catch (_: PackageManager.NameNotFoundException) {
            first
        }
        return MicGap(first, label)
    }

    /**
     * Ascolta con la schermata di dettatura del riconoscitore stesso, invece
     * che con quella di Jenny. Un'app non può chiedere un permesso per conto di
     * un'altra; la schermata del riconoscitore sì, perché è sua: al primo uso
     * chiede il microfono, trascrive e rende il testo. Dal giro dopo il
     * permesso c'è, e «Parla» torna alla schermata di Jenny.
     *
     * `false` se il riconoscitore quella schermata non ce l'ha.
     */
    private fun listenWithSystemScreen(gap: MicGap): Boolean {
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .setPackage(gap.pkg)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        return try {
            micGap = gap
            @Suppress("DEPRECATION")
            startActivityForResult(intent, REQ_SPEECH)
            true
        } catch (_: ActivityNotFoundException) {
            micGap = null
            false
        }
    }

    private fun firstResult(bundle: Bundle): String? =
        bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_AUDIO) return
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) listen()
        else polso.error(getString(R.string.mic_denied), null)
    }

    // --- Scrivi --------------------------------------------------------------

    /**
     * Apre subito la tastiera dell'orologio. La schermata di sistema per
     * l'inserimento chiede prima «emoji, voce o tastiera»: un tocco in più per
     * arrivare dove il tasto dice già di andare. Qui invece c'è un campo di
     * testo nostro, che non si vede, e la tastiera ci scrive dentro; «Invia»
     * manda la domanda.
     */
    private fun type() {
        val field = typed ?: EditText(this).also { f ->
            f.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES or
                InputType.TYPE_TEXT_FLAG_AUTO_CORRECT
            f.imeOptions = EditorInfo.IME_ACTION_SEND
            f.hint = getString(R.string.type_hint)
            f.alpha = 0f
            f.setOnEditorActionListener { v, action, _ ->
                // La tastiera dell'orologio manda «Invia» prima di consegnare
                // il testo: lo si legge un attimo dopo, non subito.
                main.postDelayed({
                    val text = v.text?.toString().orEmpty()
                    Log.i(TAG, "Typed (action=$action chars=${text.length})")
                    getSystemService(InputMethodManager::class.java)?.hideSoftInputFromWindow(v.windowToken, 0)
                    v.clearFocus()
                    if (text.isNotBlank()) ask(text)
                }, TYPED_SETTLE_MS)
                true
            }
            f.isFocusableInTouchMode = true
            polso.addView(f, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, 2))
            typed = f
        }
        field.setText("")
        field.requestFocus()
        // Dopo il giro di layout: un campo appena aggiunto non ha ancora il
        // legame con la tastiera, e la richiesta fatta subito va persa.
        field.post {
            val shown = getSystemService(InputMethodManager::class.java)?.showSoftInput(field, 0)
            Log.i(TAG, "Keyboard requested (accepted=$shown)")
        }
    }

    @Deprecated("startActivityForResult: l'activity resta una Activity semplice, senza AndroidX")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_SPEECH) {
            val gap = micGap
            micGap = null
            val text = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
            when {
                !text.isNullOrBlank() -> ask(text)
                // Ancora senza microfono: il permesso è stato negato.
                gap != null && recognizerWithoutMic() != null ->
                    polso.error(getString(R.string.recognizer_no_mic, gap.label), null)
                else -> polso.error(getString(R.string.heard_nothing), null)
            }
            return
        }
    }

    // --- La domanda e la risposta -------------------------------------------

    /** Manda *text* al telefono e aspetta. Una domanda nuova sostituisce
     *  quella in attesa: la risposta alla vecchia, se arriva, si ignora. */
    fun ask(raw: String) {
        val text = raw.trim()
        if (text.isEmpty()) return
        if (text.length > WearProtocol.MAX_TEXT_CHARS) {
            polso.error(getString(R.string.too_long), null)
            return
        }
        // Un timer o una sveglia li fa l'orologio da sé, anche col telefono
        // lontano; se non ci riesce, la frase va a Jenny come tutte le altre.
        LocalCommand.parse(text)?.let { if (runLocal(it)) return }
        cancelTimers()
        val id = UUID.randomUUID().toString()
        pendingId = id
        lastQuestion = text
        polso.thinking(text)
        Wearable.getCapabilityClient(this)
            .getCapability(WearProtocol.CAPABILITY_PHONE, CapabilityClient.FILTER_REACHABLE)
            .addOnSuccessListener { info ->
                if (pendingId != id) return@addOnSuccessListener
                val node = info.nodes.firstOrNull { it.isNearby } ?: info.nodes.firstOrNull()
                if (node == null) {
                    fail(R.string.no_phone, "no reachable node with ${WearProtocol.CAPABILITY_PHONE}")
                    return@addOnSuccessListener
                }
                Wearable.getMessageClient(this)
                    .sendMessage(node.id, WearProtocol.PATH_ASK, WearProtocol.encodeAsk(id, text))
                    .addOnSuccessListener {
                        Log.i(TAG, "Question sent (chars=${text.length})")
                        if (pendingId == id) main.postDelayed(ackTimeout, WearProtocol.ACK_TIMEOUT_MS)
                    }
                    .addOnFailureListener { e ->
                        if (pendingId == id) fail(R.string.unreachable, "send failed: ${e.javaClass.simpleName}")
                    }
            }
            .addOnFailureListener { e ->
                if (pendingId == id) fail(R.string.no_phone, "capability lookup failed: ${e.javaClass.simpleName}")
            }
    }

    /**
     * Esegue il comando con le app dell'orologio — l'Orologio per timer e
     * sveglie, la mappa per i posti, l'app chiamata per nome — e dice cosa ha
     * fatto. `false` se nessuna app lo prende: allora la frase va a Jenny.
     *
     * L'app chiamata si apre sempre sopra Jenny, anche l'Orologio: del timer si
     * vuole vedere il conto alla rovescia, e da lì lo si ferma con un tocco.
     * La conferma scritta resta sotto, per quando si torna indietro.
     */
    private fun runLocal(command: LocalCommand): Boolean {
        val (intent, done) = when (command) {
            is LocalCommand.Timer -> Intent(AlarmClock.ACTION_SET_TIMER)
                .putExtra(AlarmClock.EXTRA_LENGTH, command.seconds)
                .apply { command.label?.let { putExtra(AlarmClock.EXTRA_MESSAGE, it) } } to
                getString(R.string.timer_started, spokenDuration(command.seconds))
            is LocalCommand.Alarm -> Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, command.hour)
                .putExtra(AlarmClock.EXTRA_MINUTES, command.minute)
                .apply { command.label?.let { putExtra(AlarmClock.EXTRA_MESSAGE, it) } } to
                getString(R.string.alarm_set, "%d:%02d".format(command.hour, command.minute))
            is LocalCommand.OpenApp -> {
                val app = findApp(command.name) ?: run {
                    Log.i(TAG, "No app on the watch by that name: asking Jenny instead")
                    return false
                }
                Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                    .setComponent(app.first).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) to
                    getString(R.string.opening_app, app.second)
            }
            is LocalCommand.Navigate ->
                Intent(Intent.ACTION_VIEW, Uri.parse("google.navigation:q=" + Uri.encode(command.destination))) to
                    getString(R.string.navigating, command.destination)
            is LocalCommand.FindPlace ->
                Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + Uri.encode(command.query))) to
                    getString(R.string.finding_place, command.query)
        }
        return try {
            startActivity(intent)
            Log.i(TAG, "Local command run on the watch: ${command.javaClass.simpleName}")
            cancelTimers()
            pendingId = null
            polso.reply(done)
            speak(done)
            true
        } catch (e: RuntimeException) {
            // Nessuna app che lo prenda, o permesso negato.
            Log.i(TAG, "Local command not run (${e.javaClass.simpleName}): asking Jenny instead")
            false
        }
    }

    /**
     * L'app dell'elenco che si chiama come [name], col suo nome a schermo; fra
     * più candidate vince la corrispondenza migliore, e a parità il nome più
     * corto. `null` se nessuna gli somiglia.
     */
    private fun findApp(name: String): Pair<ComponentName, String>? =
        packageManager.queryIntentActivities(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0,
        ).asSequence()
            .filter { it.activityInfo.packageName != packageName }
            .map { Triple(it, it.loadLabel(packageManager).toString(), 0) }
            .map { (info, label, _) -> Triple(info, label, LocalCommand.appMatch(label, name)) }
            .filter { it.third > 0 }
            .sortedWith(compareByDescending<Triple<android.content.pm.ResolveInfo, String, Int>> { it.third }
                .thenBy { it.second.length })
            .firstOrNull()
            ?.let { (info, label, _) ->
                ComponentName(info.activityInfo.packageName, info.activityInfo.name) to label
            }

    /** «1 ora e 30 minuti»: le due unità più grandi che non sono zero. */
    private fun spokenDuration(seconds: Int): String {
        val parts = listOf(
            R.plurals.hours to seconds / 3600,
            R.plurals.minutes to seconds % 3600 / 60,
            R.plurals.seconds to seconds % 60,
        ).filter { it.second > 0 }.take(2).map { resources.getQuantityString(it.first, it.second, it.second) }
        return if (parts.size == 2) getString(R.string.duration_join, parts[0], parts[1]) else parts.first()
    }

    /** Sul main thread: è il default del listener del Data Layer. */
    override fun onMessageReceived(event: MessageEvent) {
        when (event.path) {
            WearProtocol.PATH_ACK -> {
                val ack = WearProtocol.decodeAck(event.data) ?: return
                if (ack.id != pendingId) return
                when {
                    ack.version != WearProtocol.VERSION -> fail(R.string.version_mismatch, "ack v=${ack.version}")
                    ack.ok -> {
                        Log.i(TAG, "Question accepted by the phone")
                        main.removeCallbacks(ackTimeout)
                        main.postDelayed(replyTimeout, WearProtocol.REPLY_TIMEOUT_MS)
                    }
                    else -> fail(
                        when (ack.error) {
                            WearProtocol.ERR_VERSION -> R.string.version_mismatch
                            WearProtocol.ERR_TOO_LONG -> R.string.too_long
                            else -> R.string.unreachable
                        },
                        "refused: ${ack.error}",
                    )
                }
            }
            WearProtocol.PATH_REPLY -> {
                val reply = WearProtocol.decodeReply(event.data) ?: return
                if (reply.id != pendingId) return
                if (reply.version != WearProtocol.VERSION) {
                    fail(R.string.version_mismatch, "reply v=${reply.version}")
                    return
                }
                cancelTimers()
                pendingId = null
                Log.i(TAG, "Reply shown (chars=${reply.text.length})")
                polso.reply(reply.text)
                speak(reply.text)
            }
        }
    }

    private fun fail(message: Int, why: String) {
        Log.i(TAG, "Question failed: $why")
        cancelTimers()
        pendingId = null
        // Toccare Jenny rimanda la stessa domanda, senza doverla ridire.
        val again = lastQuestion
        polso.error(getString(message), again?.let { q -> Runnable { ask(q) } })
    }

    private fun cancelTimers() {
        main.removeCallbacks(ackTimeout)
        main.removeCallbacks(replyTimeout)
    }

    // --- La voce -------------------------------------------------------------

    private fun ensureTts() {
        if (tts != null) return
        tts = TextToSpeech(this) { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            // Il tono è più alto del normale, scelto a orecchio sul polso: lo
            // applica il motore mentre sintetizza, e all'orologio non costa niente.
            tts?.setPitch(VOICE_PITCH)
        }.apply {
            setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(id: String?) = main.post { polso.speaking(true) }.let {}
                override fun onDone(id: String?) = main.post { polso.speaking(false) }.let {}
                override fun onStop(id: String?, interrupted: Boolean) = main.post { polso.speaking(false) }.let {}
                @Deprecated("Deprecated in Java")
                override fun onError(id: String?) = main.post { polso.speaking(false) }.let {}
            })
        }
    }

    /**
     * La voce di Jenny, scelta a orecchio fra quelle del motore di Google. Ce
     * ne sono due, la stessa persona: quella di rete, sintetizzata dai server
     * di Google, e quella locale, sintetizzata dall'orologio. Si usa la prima
     * finché c'è internet e la seconda quando manca, così Jenny non resta
     * muta. Se nessuna delle due esiste (altra lingua, altro motore) resta la
     * voce predefinita.
     */
    private fun chooseVoice() {
        val engine = tts ?: return
        val names = JENNY_VOICES[java.util.Locale.getDefault().language] ?: return
        val wanted = if (online()) names.first else names.second
        if (engine.voice?.name == wanted) return
        engine.voices.orEmpty().firstOrNull { it.name == wanted }?.let {
            engine.voice = it
            Log.i(TAG, "Voice: $wanted")
        }
    }

    private fun online(): Boolean {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return false
        val caps = cm.getNetworkCapabilities(cm.activeNetwork ?: return false) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }

    private fun prefs() = getSharedPreferences("jenny_wear", MODE_PRIVATE)

    private fun speak(shown: String) {
        if (!voiceOn) return
        hush()
        // A voce va solo il testo: i segni dell'impaginazione e le emoji restano a schermo.
        val text = Spoken.clean(shown)
        if (text.isEmpty()) return
        // Chi ha impostato una voce sua sente quella; se il suo server non
        // risponde, parla la voce dell'orologio, come senza impostazione.
        if (ownVoice.configured) {
            polso.voiceComing(true)
            ownVoice.speak(
                text,
                onStart = { polso.speaking(true) },
                onEnd = { polso.speaking(false) },
                onFail = { rest -> speakWithEngine(rest) },
            )
        } else {
            speakWithEngine(text)
        }
    }

    private fun speakWithEngine(text: String) {
        if (!ttsReady) {
            polso.voiceComing(false)
            return
        }
        polso.voiceComing(true)
        chooseVoice()
        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "reply")
    }

    /** Zittisce Jenny, qualunque voce stia usando. */
    private fun hush() {
        ownVoice.stop()
        tts?.stop()
        polso.speaking(false)
    }

    companion object {
        private const val TAG = "JennyWear"
        private const val REQ_AUDIO = 1
        private const val REQ_SPEECH = 3
        private const val PREF_VOICE_ON = "voice_on"
        private const val TYPED_SETTLE_MS = 250L

        /** La voce di Jenny per lingua dell'orologio: quella di rete, e la stessa in locale. */
        private val JENNY_VOICES = mapOf("it" to ("it-it-x-itb-network" to "it-it-x-itb-local"))
        private const val VOICE_PITCH = 1.25f

        /** Nell'intent che apre l'activity: comincia subito ad ascoltare. */
        const val EXTRA_LISTEN = "com.flagdizero.jenny.wear.LISTEN"

        /** L'activity a schermo, per [DebugAskReceiver]. */
        @Volatile
        private var active: WeakReference<AskActivity>? = null

        fun debugAsk(text: String): Boolean {
            val activity = active?.get() ?: return false
            activity.runOnUiThread { activity.ask(text) }
            return true
        }

        /**
         * Le voci del motore vocale nella lingua dell'orologio, nel log; con
         * [voice] ne prova una dicendo [text]. Serve a sceglierle a orecchio:
         * Android delle voci dà solo un codice, non dice che voce è.
         */
        fun debugVoice(voice: String?, text: String, pitch: Float, file: String? = null): Boolean {
            val activity = active?.get() ?: return false
            activity.runOnUiThread {
                var engine: TextToSpeech? = null
                engine = TextToSpeech(activity) { status ->
                    val tts = engine ?: return@TextToSpeech
                    if (status != TextToSpeech.SUCCESS) {
                        Log.i(TAG, "Voices: engine failed ($status)")
                        return@TextToSpeech
                    }
                    val language = java.util.Locale.getDefault().language
                    val voices = tts.voices.orEmpty().filter { it.locale.language == language }.sortedBy { it.name }
                    if (voice == null) {
                        Log.i(TAG, "Voices: engine=${tts.defaultEngine} default=${tts.defaultVoice?.name}")
                        voices.forEach {
                            Log.i(TAG, "Voice ${it.name} locale=${it.locale} quality=${it.quality} " +
                                "latency=${it.latency} network=${it.isNetworkConnectionRequired} " +
                                "installed=${!it.features.orEmpty().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED)}")
                        }
                        tts.shutdown()
                    } else {
                        val chosen = voices.firstOrNull { it.name == voice }
                        Log.i(TAG, "Voice trial: $voice found=${chosen != null}")
                        if (chosen != null) tts.voice = chosen
                        tts.setPitch(pitch)
                        if (file == null) {
                            tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "trial")
                        } else {
                            // In un file invece che dall'altoparlante, per
                            // confrontare le voci lontano dall'orologio.
                            tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                                override fun onStart(id: String?) {}
                                override fun onDone(id: String?) {
                                    Log.i(TAG, "Voice file done: $file")
                                    tts.shutdown()
                                }
                                @Deprecated("Deprecated in Java")
                                override fun onError(id: String?) {
                                    Log.i(TAG, "Voice file failed: $file")
                                    tts.shutdown()
                                }
                            })
                            tts.synthesizeToFile(text, null, java.io.File(activity.cacheDir, file), "trial-file")
                        }
                    }
                }
            }
            return true
        }

        /** Mostra una schermata senza passare dal telefono, per fotografarla. */
        fun debugShow(view: String, text: String): Boolean {
            val activity = active?.get() ?: return false
            activity.runOnUiThread {
                when (view) {
                    "pronta" -> activity.polso.ready()
                    "ascolto" -> activity.polso.listening(text)
                    "penso" -> activity.polso.thinking(text)
                    "risposta" -> {
                        activity.polso.reply(text)
                        activity.speak(text)
                    }
                    "errore" -> activity.polso.error(text, null)
                }
            }
            return true
        }
    }
}
