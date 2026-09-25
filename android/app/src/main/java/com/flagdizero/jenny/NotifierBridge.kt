package com.flagdizero.jenny

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.util.Log
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.Person
import androidx.core.app.RemoteInput
import androidx.core.graphics.drawable.IconCompat

/**
 * Bridge per gli alert di sistema (le notifiche che squillano), esposto a
 * Python via Chaquopy (`jclass("com.flagdizero.jenny.NotifierBridge")`), mai
 * istanziato da Kotlin — stesso pattern di InstalledAppsBridge.
 *
 * Usa un canale dedicato `jenny_alerts` (IMPORTANCE_HIGH, suono/vibrazione di
 * sistema) separato dal canale silenzioso del foreground service, così
 * l'utente può personalizzarne la suoneria dalle impostazioni Android senza
 * toccare la notifica persistente del gateway.
 *
 * Gate di visibilità: se MainActivity è in foreground l'alert viene soppresso
 * (il messaggio è già visibile in chat) — la policy "se squillare" vive qui,
 * quella "cosa dire" vive in Python (jenny/runtime/notifier.py).
 */
class NotifierBridge(context: Context) {

    companion object {
        private const val TAG = "NotifierBridge"
        private const val CHANNEL_ID = "jenny_alerts"
        // ID fisso + tag variabile: alert con lo stesso tag si sostituiscono
        // (niente pila infinita per la stessa sorgente), tag diversi convivono.
        // `internal` perché `ReplyReceiver` cancella con questo ID l'alert a cui
        // si è appena risposto.
        internal const val ALERT_ID = 2

        /** Chiave del testo dentro il bundle di `RemoteInput`. La legge
         *  `ReplyReceiver` e non esiste altrove: è il nome con cui il sistema ci
         *  restituisce ciò che l'utente ha scritto. */
        internal const val KEY_REPLY_TEXT = "com.flagdizero.jenny.reply.TEXT"

        /** Tag dell'alert a cui si sta rispondendo, cotto nel PendingIntent
         *  della risposta: serve a `ReplyReceiver` per cancellare *quello* e non
         *  tutti. */
        internal const val EXTRA_REPLY_SOURCE_TAG = "com.flagdizero.jenny.reply.SOURCE_TAG"

        /** Testo da riconsegnare, presente **solo** sull'intent del "Rimanda"
         *  della notifica di mancata consegna: là il testo non arriva da
         *  `RemoteInput` (l'utente l'ha già scritto una volta) e viaggia
         *  nell'extra. */
        internal const val EXTRA_REPLY_RETRY_TEXT = "com.flagdizero.jenny.reply.RETRY_TEXT"

        /** Tag della notifica "non ho ricevuto il messaggio". Distinto da quelli
         *  degli alert: non è un avviso di Jenny, è un guasto da riparare, e
         *  sostituirlo con l'avviso successivo lo farebbe sparire non letto. */
        internal const val FAILED_TAG = "reply-failed"

        /** Request code del PendingIntent di risposta.
         *
         *  L'identità di un `PendingIntent` è (context, requestCode, intent,
         *  flags), e `postAlert` usa già `tag.hashCode()` per il content intent:
         *  riusare lo stesso numero qui significherebbe che i due si
         *  sovrascrivono a vicenda sotto `FLAG_UPDATE_CURRENT`. Stessa cura, e
         *  stessa ragione, del `OPEN_UI_REQUEST_CODE = 1001` di `GatewayService`.
         *  Lo XOR è con "REPL" in ASCII: un numero qualunque, ma leggibile. */
        private fun replyRequestCode(tag: String): Int = tag.hashCode() xor 0x5245504C

        /** Flag del PendingIntent di risposta.
         *
         *  **`FLAG_MUTABLE` non è un dettaglio.** Il sistema deve poter scrivere
         *  il testo dell'utente dentro l'intent prima di consegnarlo: con
         *  `FLAG_IMMUTABLE` — che è ciò che il content intent usa, e
         *  correttamente — il bundle di `RemoteInput` arriva **vuoto**, senza
         *  alcun errore. La risposta sembra inviata e non esiste.
         *  Sotto API 31 la costante non c'è e il default è già mutabile. */
        private fun replyIntentFlags(): Int =
            PendingIntent.FLAG_UPDATE_CURRENT or
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0

        /** Crea il canale degli alert se non c'è.
         *
         *  Nel companion perché non la usa solo il costruttore del bridge:
         *  `postReplyFailure` gira quando il gateway **non** è partito, cioè
         *  proprio quando nessuno ha ancora costruito un `NotifierBridge` — e
         *  una notifica su un canale inesistente non compare e non dice perché. */
        internal fun ensureAlertChannel(context: Context) {
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            val channel = NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.alerts_channel_name),
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = context.getString(R.string.alerts_channel_description)
            }
            manager.createNotificationChannel(channel)
        }

        /**
         * L'azione "Rispondi" da appendere a un alert.
         *
         * Ogni alert del canale la porta, non solo le risposte dell'agente: a un
         * promemoria del cron si replica come a qualunque altra cosa Jenny dica,
         * e una regola sola non può divergere da se stessa.
         *
         * `setShowsUserInterface(false)` tiene la risposta *dentro* la tendina —
         * senza, alcune shell aprono l'app, che è l'unica cosa che questa
         * funzione doveva evitare. `setAuthenticationRequired(true)` pretende lo
         * sblocco: in questa conversazione c'è tutto ciò che Jenny sa
         * dell'utente, e la tendina la legge chiunque prenda il telefono dal
         * tavolo. `setAllowGeneratedReplies(false)` toglie le risposte
         * suggerite dal sistema: metterebbero parole in bocca all'utente, e il
         * modello le leggerebbe come sue.
         */
        internal fun replyAction(context: Context, tag: String): NotificationCompat.Action {
            val remoteInput = RemoteInput.Builder(KEY_REPLY_TEXT)
                .setLabel(context.getString(R.string.reply_input_hint))
                .build()
            val intent = Intent(context, ReplyReceiver::class.java)
                .putExtra(EXTRA_REPLY_SOURCE_TAG, tag)
            val pending = PendingIntent.getBroadcast(
                context, replyRequestCode(tag), intent, replyIntentFlags()
            )
            return NotificationCompat.Action.Builder(
                R.drawable.ic_stat_jenny,
                context.getString(R.string.reply_action_label),
                pending
            )
                .addRemoteInput(remoteInput)
                .setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_REPLY)
                .setShowsUserInterface(false)
                .setAllowGeneratedReplies(false)
                .setAuthenticationRequired(true)
                .build()
        }

        /**
         * Dice che un messaggio scritto nella tendina **non** è arrivato.
         *
         * Il terzo esito, e la ragione per cui questo percorso non somiglia a
         * quello dei tick di sveglia: un tick perso lo recupera il giro dopo, le
         * parole dell'utente no. Invece di accodarle al buio — una consegna a
         * sorpresa tre ore dopo è peggio di nessuna consegna — le si restituisce
         * con un "Rimanda" che le riporta dentro allo stesso `ReplyReceiver`.
         *
         * Il testo viaggia nell'extra del PendingIntent, cioè in memoria del
         * sistema, e non in un file: un messaggio dell'utente parcheggiato su
         * disco è un dato personale in più da cancellare. Per la stessa ragione
         * questa notifica sopravvive a `clearAlerts` (v. là): è l'unica copia
         * di quelle parole finché l'utente non le rimanda.
         */
        internal fun postReplyFailure(context: Context, text: String, sourceTag: String?) {
            val appContext = context.applicationContext
            try {
                ensureAlertChannel(appContext)
                val retryIntent = Intent(appContext, ReplyReceiver::class.java)
                    .putExtra(EXTRA_REPLY_RETRY_TEXT, text)
                    .putExtra(EXTRA_REPLY_SOURCE_TAG, sourceTag)
                val retry = PendingIntent.getBroadcast(
                    appContext,
                    replyRequestCode(FAILED_TAG),
                    retryIntent,
                    // Immutabile: qui il testo lo mettiamo noi e nessuno deve
                    // poterlo cambiare — è l'opposto esatto del caso RemoteInput.
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                )
                val notification = NotificationCompat.Builder(appContext, CHANNEL_ID)
                    .setContentTitle(appContext.getString(R.string.reply_failed_title))
                    .setContentText(appContext.getString(R.string.reply_failed_text))
                    .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                    .setSmallIcon(R.drawable.ic_stat_jenny)
                    .setAutoCancel(true)
                    .setPriority(NotificationCompat.PRIORITY_HIGH)
                    .setCategory(NotificationCompat.CATEGORY_ERROR)
                    .addAction(
                        R.drawable.ic_stat_jenny,
                        appContext.getString(R.string.reply_retry_label),
                        retry
                    )
                    .build()
                appContext.getSystemService(NotificationManager::class.java)
                    ?.notify(FAILED_TAG, ALERT_ID, notification)
                Log.i(TAG, "Reply failure notice posted (chars=${text.length})")
            } catch (e: Exception) {
                Log.e(TAG, "Could not post the reply failure notice", e)
            }
        }

        /** Quanti messaggi tiene la conversazione nella tendina.
         *
         *  Non è un limite estetico: gli extras di una notifica viaggiano in un
         *  `Bundle` attraverso il confine di processo a ogni `notify`, e un filo
         *  che cresce senza fine è un payload che cresce senza fine. Il resto
         *  della conversazione si legge in chat, che è la storia vera. */
        private const val MAX_CONVERSATION_MESSAGES = 10

        /** Jenny, come interlocutore. La `key` tiene ferma l'identità fra una
         *  ricostruzione e l'altra: senza, ogni `addMessage` rischia di sembrare
         *  un mittente nuovo. */
        private fun jennyPerson(context: Context): Person =
            Person.Builder()
                .setName(context.getString(R.string.reply_bot_name))
                .setKey("jenny")
                .setIcon(
                    IconCompat.createWithResource(context, R.drawable.ic_notification_large)
                )
                .build()

        /** L'utente. Un messaggio aggiunto con `person = null` è suo per
         *  contratto di `MessagingStyle`; questo serve a dargli un nome quando
         *  il sistema decide di mostrarlo. */
        private fun userPerson(context: Context): Person =
            Person.Builder()
                .setName(context.getString(R.string.reply_user_name))
                .setKey("me")
                .build()

        /** La notifica viva su quel tag, se c'è.
         *
         *  **È qui che vive la conversazione.** Non c'è un anello in memoria né
         *  niente su disco: i messaggi stanno negli extras della notifica che
         *  abbiamo già postato, e si rileggono da lì. Ne segue il ciclo di vita
         *  giusto senza scriverlo: scartata la notifica — dal dito dell'utente o
         *  da `clearAlerts` quando la chat arriva a schermo — il filo finisce, e
         *  il prossimo avviso ne apre uno nuovo. */
        private fun activeAlert(context: Context, tag: String): Notification? =
            context.getSystemService(NotificationManager::class.java)
                ?.activeNotifications
                ?.firstOrNull { it.id == ALERT_ID && it.tag == tag }
                ?.notification

        /** Taglia il filo agli ultimi `MAX_CONVERSATION_MESSAGES`.
         *
         *  `MessagingStyle` non sa togliere un messaggio, quindi si ricostruisce
         *  con la coda. Il `Person` dell'utente si porta dietro dal vecchio
         *  stile: è l'identità del filo, non un dettaglio di rendering. */
        private fun capped(style: NotificationCompat.MessagingStyle):
            NotificationCompat.MessagingStyle {
            val messages = style.messages
            if (messages.size <= MAX_CONVERSATION_MESSAGES) return style
            val trimmed = NotificationCompat.MessagingStyle(style.user)
            messages.takeLast(MAX_CONVERSATION_MESSAGES).forEach { trimmed.addMessage(it) }
            return trimmed
        }

        /** La parte di notifica che non dipende da che cosa c'è dentro.
         *
         *  Estratta perché ora i posti che costruiscono un alert sono due — quello
         *  di sempre e la conversazione — e l'icona, l'intent del tocco e
         *  soprattutto **l'azione Rispondi** devono restare identici: una
         *  conversazione a cui non si può rispondere sarebbe un vicolo cieco.
         */
        private fun baseBuilder(context: Context, tag: String): NotificationCompat.Builder {
            // L'action non è decorativa: è l'unica cosa che distingue questo
            // intent da un rilancio qualunque dell'activity. Senza,
            // MainActivity.onNewIntent — che instrada solo CATEGORY_HOME — non
            // aveva niente da riconoscere e il tap riportava l'app dov'era,
            // mini-app aperta compresa, invece che in chat.
            val intent = Intent(context, MainActivity::class.java)
                .setAction(MainActivity.ACTION_OPEN_CHAT)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val pending = PendingIntent.getActivity(
                context,
                tag.hashCode(),
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            return NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_jenny)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                .setContentIntent(pending)
                .addAction(replyAction(context, tag))
        }

        /** Ripubblica la conversazione sullo stesso tag.
         *
         *  *silent* distingue i due motivi per cui si riposta: l'eco di ciò che
         *  ha appena scritto l'utente non deve suonare (sta guardando la
         *  tendina, l'ha appena toccata), la risposta di Jenny sì — è la notizia
         *  per cui la notifica esiste.
         */
        private fun postConversation(
            context: Context,
            tag: String,
            style: NotificationCompat.MessagingStyle,
            silent: Boolean,
        ) {
            ensureAlertChannel(context)
            val notification = baseBuilder(context, tag)
                .setStyle(capped(style))
                .setOnlyAlertOnce(silent)
                .build()
            context.getSystemService(NotificationManager::class.java)
                ?.notify(tag, ALERT_ID, notification)
        }

        /**
         * Aggiunge un messaggio a una conversazione già aperta su *tag*.
         *
         * `false` quando su quel tag non c'è niente, o c'è un avviso che non è
         * ancora una conversazione: il chiamante sa così che deve postare un
         * alert normale. Nessuna migrazione da scrivere per le notifiche nate
         * prima di questa versione — `extractMessagingStyleFromNotification`
         * ritorna `null` e si ricade sul percorso di sempre.
         */
        internal fun appendToConversation(
            context: Context,
            tag: String,
            text: String,
            from: Person?,
            silent: Boolean,
        ): Boolean {
            val appContext = context.applicationContext
            return try {
                val existing = activeAlert(appContext, tag) ?: return false
                val style = NotificationCompat.MessagingStyle
                    .extractMessagingStyleFromNotification(existing) ?: return false
                style.addMessage(text, System.currentTimeMillis(), from)
                postConversation(appContext, tag, style, silent)
                Log.i(TAG, "Conversation continued (tag=$tag, chars=${text.length})")
                true
            } catch (e: Exception) {
                Log.e(TAG, "Could not continue the conversation on $tag", e)
                false
            }
        }

        /**
         * Trasforma l'avviso su *tag* in una conversazione, con dentro la
         * risposta che l'utente ha appena scritto.
         *
         * Chiamata da `ReplyReceiver`, e sostituisce la cancellazione che stava
         * lì prima. Cancellare era un ripiego per non lasciare in giro un avviso
         * già evaso; questo fa di meglio e risolve da sé
         * `LIFETIME_EXTENDED_BY_DIRECT_REPLY` — Android tiene viva la notifica a
         * cui si è risposto **proprio perché** si aspetta che l'app la aggiorni.
         *
         * Il primo messaggio del filo è il corpo dell'avviso originale. Se il suo
         * titolo portava un'etichetta (`Jenny ⏰ spesa`), l'etichetta finisce
         * davanti al testo e non in `setConversationTitle`: quel campo Android lo
         * tratta come il nome di un gruppo e su un uno-a-uno lo rende male o lo
         * ignora, mentre "quale promemoria" deve sopravvivere alla conversione.
         */
        internal fun startConversation(context: Context, tag: String, reply: String): Boolean {
            val appContext = context.applicationContext
            return try {
                val existing = activeAlert(appContext, tag) ?: return false
                val opened = NotificationCompat.MessagingStyle
                    .extractMessagingStyleFromNotification(existing)
                // Il filo può essere già aperto (si sta rispondendo a una
                // risposta) o no (si sta rispondendo a un avviso). I due casi si
                // loggano distinti, perché a schermo si somigliano e in diagnosi
                // no: se la conversione non avvenisse mai, tutto sembrerebbe
                // funzionare finché non si guarda quante schede ci sono.
                val style = opened ?: conversationFrom(appContext, existing)
                // `null as Person?` e non `null`: le due `addMessage` — una per
                // `Person`, una per `CharSequence` — sono ambigue su un null
                // nudo. Null significa "questo messaggio è dell'utente", che è
                // il contratto di MessagingStyle.
                style.addMessage(reply, System.currentTimeMillis(), null as Person?)
                postConversation(appContext, tag, style, silent = true)
                val what =
                    if (opened == null) "Alert turned into a conversation"
                    else "Reply added to the conversation"
                Log.i(TAG, "$what (tag=$tag)")
                true
            } catch (e: Exception) {
                Log.e(TAG, "Could not turn the alert on $tag into a conversation", e)
                false
            }
        }

        /** Il filo nuovo che nasce da un avviso: un messaggio solo, quello. */
        private fun conversationFrom(
            context: Context,
            alert: Notification,
        ): NotificationCompat.MessagingStyle {
            val extras = alert.extras
            val body = (extras?.getCharSequence(NotificationCompat.EXTRA_BIG_TEXT)
                ?: extras?.getCharSequence(NotificationCompat.EXTRA_TEXT))
                ?.toString()
                .orEmpty()
            val title = extras?.getCharSequence(NotificationCompat.EXTRA_TITLE)?.toString().orEmpty()
            val label = title.removePrefix(context.getString(R.string.app_name))
                .trim()
                .trimStart('·', '-', '—')
                .trim()
            val first = if (label.isEmpty()) body else "$label · $body"
            val style = NotificationCompat.MessagingStyle(userPerson(context))
            if (first.isNotEmpty()) {
                style.addMessage(first, alert.`when`, jennyPerson(context))
            }
            return style
        }

        /** Cancella gli alert pendenti del canale.
         *
         *  La regola è una sola, e vale la pena scriverla per esteso perché è
         *  già stata sbagliata in due modi opposti: **si cancella quando la
         *  chat è a schermo**, perché è lì che il messaggio si legge, e mai
         *  perché l'app è tornata in primo piano.
         *
         *  Prima stava in ``onResume`` liscio: questa app è la home del
         *  telefono, ``onResume`` scatta a ogni ritorno alla schermata iniziale
         *  e su qualunque vista, quindi l'alert veniva cancellato comunque —
         *  letto o no. Poi solo sul tap dell'alert (``ACTION_OPEN_CHAT``), che
         *  è l'errore opposto: chi apriva la chat da sé si ritrovava in coda
         *  notifiche di messaggi che aveva davanti agli occhi.
         *
         *  I tre chiamanti sono quindi i tre modi in cui la chat arriva a
         *  schermo: il tap sull'alert (``onNewIntent`` e il suo gemello in
         *  ``onCreate`` per l'activity morta), il cambio vista dentro la SPA
         *  (``JennyNative.chatOpened``: in officina da ``ChatController.activate``,
         *  in casa quando la pagina della chat torna a schermo) e il
         *  rientro in primo piano a chat **già** attiva (``onResume``, dietro
         *  la domanda ``CHAT_ON_SCREEN_JS`` — che è ciò che lo distingue
         *  dall'``onResume`` liscio di allora).
         *
         *  Non tocca la notifica persistente del gateway, che vive su un altro
         *  canale. */
        fun clearAlerts(context: Context, trigger: String) {
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            val pending = manager.activeNotifications.filter {
                // La notifica di mancata consegna resta. Non è un messaggio da
                // leggere — è un testo dell'utente che non è arrivato, con il
                // pulsante per rimandarlo — quindi "la chat è a schermo" non la
                // rende obsoleta: cancellarla butterebbe l'unica copia di quelle
                // parole. La toglie chi la usa (`ReplyReceiver`, sul "Rimanda")
                // o il dito dell'utente.
                it.notification.channelId == CHANNEL_ID && it.tag != FAILED_TAG
            }
            pending.forEach { manager.cancel(it.tag, it.id) }
            // Il *trigger* è il motivo per cui questa riga esiste. Postare logga
            // solo i soppressi, e cancellare non loggava niente: da fuori
            // "l'alert non c'è più" ha quattro spiegazioni indistinguibili — uno
            // dei tre percorsi qui sotto, o il dito dell'utente sulla tendina.
            // Misurato il 27/08/2026: la prima verifica di questa funzione è
            // stata inconcludente esattamente per questo.
            Log.d(TAG, "clearAlerts($trigger): cancelled ${pending.size} alert(s)")
        }
    }

    private val appContext = context.applicationContext

    init {
        ensureChannel()
    }

    private fun ensureChannel() = ensureAlertChannel(appContext)

    /**
     * Posta un alert che squilla. Ritorna false quando soppresso (app in
     * foreground), permesso mancante o errore — il chiamante Python logga e
     * basta, il messaggio resta comunque in chat.
     */
    fun postAlert(title: String, body: String, tag: String): Boolean {
        if (MainActivity.isInForeground) {
            Log.d(TAG, "Alert suppressed: app in foreground")
            return false
        }
        // Se su questo tag è già aperto un filo, questa è la sua continuazione e
        // non una scheda nuova. È il caso normale di una risposta: la domanda
        // l'utente l'ha scritta *dentro* quella notifica, e la risposta deve
        // comparire lì sotto.
        if (appendToConversation(appContext, tag, body, jennyPerson(appContext), silent = false)) {
            return true
        }
        return try {
            val notification = baseBuilder(appContext, tag)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setLargeIcon(
                    BitmapFactory.decodeResource(appContext.resources, R.drawable.ic_notification_large)
                )
                .build()
            val manager = appContext.getSystemService(NotificationManager::class.java)
                ?: return false
            manager.notify(tag, ALERT_ID, notification)
            true
        } catch (e: Exception) {
            // Include SecurityException su API 33+ senza POST_NOTIFICATIONS.
            Log.e(TAG, "Failed to post alert", e)
            false
        }
    }
}
