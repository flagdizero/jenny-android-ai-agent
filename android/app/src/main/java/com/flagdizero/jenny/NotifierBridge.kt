package com.flagdizero.jenny

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
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

        /** Il canale delle notifiche che la pillola mostra già a schermo:
         *  importanza bassa, quindi mai a comparsa sopra l'app. `setSilent` non
         *  bastava: con due notifiche, Android le raggruppava e il gruppo
         *  scendeva lo stesso sopra l'Orologio (Titan, 23/11/2026). */
        private const val QUIET_CHANNEL_ID = "jenny_phone"
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

        /** ID delle notifiche dell'altra Jenny (la conferma «Consenti»/«No» e il
         *  «fatto» con «Annulla»). Sullo stesso canale degli alert ma con un ID
         *  suo, ed è questo a tenerle fuori da [clearAlerts] e da
         *  [refreshAlertTapIntents]: non sono messaggi che la chat a schermo
         *  rende letti, sono una decisione da prendere o un'azione da poter
         *  disfare, e cancellarle butterebbe i loro pulsanti. Non c'è nemmeno
         *  la Rispondi: una frase scritta lì sotto non risponderebbe a niente. */
        internal const val PEER_NOTICE_ID = 5

        /** I due tipi di notifica dell'altra Jenny. Valori di protocollo,
         *  gemelli di `PEER_NOTICE_KINDS` in `jenny/runtime/notifier.py`. */
        internal const val PEER_KIND_CONFIRM = "confirm"
        internal const val PEER_KIND_DONE = "done"

        /** Il tag di una notifica dell'altra Jenny: il tipo e l'identificativo
         *  (`cid` di una conferma, `qid` di una richiesta). Lo stesso tag
         *  sostituisce, quindi ripostare lo stesso `qid` aggiorna e non impila. */
        internal fun peerTag(kind: String, ident: String): String = "peer-$kind:$ident"

        /** Toglie la notifica dell'altra Jenny di *kind* e *ident*. La chiamano il
         *  bridge (da Python, a decisione presa o scaduta) e `PeerActionReceiver`
         *  al tocco: i pulsanti d'azione non si auto-cancellano. */
        internal fun cancelPeerNotice(context: Context, kind: String, ident: String) {
            context.applicationContext.getSystemService(NotificationManager::class.java)
                ?.cancel(peerTag(kind, ident), PEER_NOTICE_ID)
        }

        /**
         * Il `PendingIntent` di un pulsante dell'altra Jenny.
         *
         * Immutabile, come il "Rimanda" di [postReplyFailure] e per la stessa
         * ragione: azione e identificativo li mettiamo noi, e nessuno deve
         * poterli cambiare. Non lo è invece **chi può premerlo**: un'app con
         * l'accesso alle notifiche vede le azioni e può mandarle, «Consenti»
         * compreso, e il suo `send()` non passa dallo sblocco che
         * `setAuthenticationRequired` chiede al dito. Il confine vero è quindi
         * l'accesso alle notifiche, come per `ReplyReceiver` (v. il suo KDoc).
         *
         * L'identità di un `PendingIntent` sono (requestCode, action, data,
         * componente), **non** gli extra: due pulsanti con lo stesso codice si
         * fonderebbero in uno solo, l'ultimo scritto. Per questo azione e
         * identificativo stanno anche nella `data` dell'intent: con quella,
         * nemmeno due hash che collidono possono scambiarsi il `cid`.
         */
        private fun peerActionIntent(context: Context, action: String, ident: String): PendingIntent {
            val intent = Intent(context, PeerActionReceiver::class.java)
                .setAction(action)
                .setData(Uri.parse("jenny-peer://$action/$ident"))
                .putExtra(PeerActionReceiver.EXTRA_ACTION, action)
                .putExtra(PeerActionReceiver.EXTRA_IDENT, ident)
            return PendingIntent.getBroadcast(
                context,
                "$action:$ident".hashCode(),
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        }

        /** Un pulsante dell'altra Jenny. *needsUnlock* per «Consenti» e
         *  «Annulla», che fanno qualcosa: la tendina la legge chiunque prenda il
         *  telefono dal tavolo. «No» non lo chiede — rifiutare non fa uscire
         *  niente. */
        private fun peerAction(
            context: Context,
            label: Int,
            action: String,
            ident: String,
            needsUnlock: Boolean,
        ): NotificationCompat.Action =
            NotificationCompat.Action.Builder(
                R.drawable.ic_stat_jenny,
                context.getString(label),
                peerActionIntent(context, action, ident)
            )
                .setShowsUserInterface(false)
                .setAuthenticationRequired(needsUnlock)
                .build()

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
         *  Sotto API 31 la costante non c'è e il default è già mutabile.
         *
         *  Il prezzo, da non dimenticare: mutabile vuol dire che **chiunque tenga
         *  il PendingIntent** ne riempie gli extra. Il componente è esplicito e
         *  non si dirotta, ma un'app con l'accesso alle notifiche può mandarlo
         *  con un testo suo, che arriva a Jenny come dell'utente (v. il KDoc di
         *  `ReplyReceiver`). È il contratto della risposta diretta di Android,
         *  non un buco da chiudere qui. */
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
            JennyChannels.ensure(
                context, CHANNEL_ID, R.string.alerts_channel_name,
                R.string.alerts_channel_description, NotificationManager.IMPORTANCE_HIGH,
            )
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
         *
         *  Tranne in un caso, ed è voluto: **l'alert di un quaderno non ha
         *  Rispondi.** La risposta dalla tendina entra nella chat personale
         *  (`session_key_for_channel`: la vita fuori di Jenny non entra in un
         *  quaderno), e una frase scritta sotto il nome di un quaderno che
         *  finisce altrove è peggio di nessuna azione. Lì la strada è il tocco,
         *  che porta nel quaderno (v. [AlertConversation]).
         */
        private fun baseBuilder(context: Context, tag: String): NotificationCompat.Builder {
            // L'action non è decorativa: è l'unica cosa che distingue questo
            // intent da un rilancio qualunque dell'activity. Senza,
            // MainActivity.onNewIntent — che instrada solo CATEGORY_HOME — non
            // aveva niente da riconoscere e il tap riportava l'app dov'era,
            // mini-app aperta compresa, invece che in chat. Porta anche il
            // gettone che la distingue da un'action scritta da un'altra app.
            val builder = NotificationCompat.Builder(context, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_jenny)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                .setContentIntent(alertTapIntent(context, tag))
            if (AlertConversation.ofTag(tag) == null) builder.addAction(replyAction(context, tag))
            return builder
        }

        /** Il tocco sull'alert di *tag*: l'intent che porta in chat — quella del
         *  quaderno, se il tag ne nomina uno ([AlertConversation.ofTag]) —
         *  gettone compreso. Lo stesso `requestCode` e gli stessi flag a ogni chiamata,
         *  ed e' questo che lo rende riscrivibile: un `PendingIntent` si
         *  riconosce da codice, intent (action e componente, non gli extra) e
         *  flag, e con `FLAG_UPDATE_CURRENT` una seconda richiesta uguale
         *  **aggiorna gli extra di quello che c'e'**, anche dentro una notifica
         *  gia' in tendina (v. [refreshAlertTapIntents]). */
        private fun alertTapIntent(context: Context, tag: String): PendingIntent =
            PendingIntent.getActivity(
                context,
                tag.hashCode(),
                MainActivity.openChatIntent(context, AlertConversation.ofTag(tag)),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )

        /** Mette il gettone negli alert che sono gia' in tendina.
         *
         *  Il tocco su un alert porta in chat (e cancella gli avvisi) solo se
         *  l'intent ha il gettone di `MainActivity.openChatIntent`. Un alert
         *  postato da una versione che il gettone non lo metteva, e ancora in
         *  tendina dopo l'aggiornamento, al tocco apriva l'app dov'era: non la
         *  chat. Non si ripubblica niente — ne' suono ne' testo da ricostruire:
         *  quella versione usava lo stesso `requestCode` (`tag.hashCode()`), la
         *  stessa action e gli stessi flag, quindi chiedere di nuovo il
         *  `PendingIntent` di ogni tag ne riscrive gli extra al suo posto (v.
         *  [alertTapIntent]).
         *
         *  La notifica di mancata consegna non ha un tocco da riscrivere, e
         *  si salta. La chiama `BootReceiver` dopo un aggiornamento. */
        fun refreshAlertTapIntents(context: Context) {
            try {
                val manager = context.getSystemService(NotificationManager::class.java) ?: return
                val tags = manager.activeNotifications
                    .filter {
                        it.notification.channelId == CHANNEL_ID && it.id == ALERT_ID &&
                            it.tag != FAILED_TAG
                    }
                    .mapNotNull { it.tag }
                    .distinct()
                tags.forEach { alertTapIntent(context, it) }
                if (tags.isNotEmpty()) Log.i(TAG, "Alert tap intents refreshed: ${tags.size}")
            } catch (e: Exception) {
                // Un alert vecchio che al tocco non porta in chat non vale un
                // crash del ricevitore che rimette in piedi il gateway.
                Log.w(TAG, "Alert tap intents not refreshed (${e.javaClass.simpleName})")
            }
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
         *  canale.
         *
         *  Cancella solo gli alert di *conversation*, la chat arrivata a schermo
         *  (`null`: la personale): aprire un quaderno non dice niente degli
         *  avvisi personali, né la chat personale di quelli di un quaderno. */
        fun clearAlerts(context: Context, trigger: String, conversation: String? = null) {
            val manager = context.getSystemService(NotificationManager::class.java) ?: return
            val pending = manager.activeNotifications.filter {
                AlertConversation.ofTag(it.tag) == conversation &&
                // La notifica di mancata consegna resta. Non è un messaggio da
                // leggere — è un testo dell'utente che non è arrivato, con il
                // pulsante per rimandarlo — quindi "la chat è a schermo" non la
                // rende obsoleta: cancellarla butterebbe l'unica copia di quelle
                // parole. La toglie chi la usa (`ReplyReceiver`, sul "Rimanda")
                // o il dito dell'utente.
                it.notification.channelId == CHANNEL_ID && it.tag != FAILED_TAG &&
                // Le notifiche dell'altra Jenny restano anche loro, con un ID
                // proprio: v. `PEER_NOTICE_ID`.
                it.id == ALERT_ID
            }
            pending.forEach { manager.cancel(it.tag, it.id) }
            // Il *trigger* è il motivo per cui questa riga esiste. Postare logga
            // solo i soppressi, e cancellare non loggava niente: da fuori
            // "l'alert non c'è più" ha quattro spiegazioni indistinguibili — uno
            // dei tre percorsi qui sotto, o il dito dell'utente sulla tendina.
            // Misurato il 27/08/2026: la prima verifica di questa funzione è
            // stata inconcludente esattamente per questo.
            Log.d(
                TAG,
                "clearAlerts($trigger, ${conversation ?: "personal"}): cancelled ${pending.size} alert(s)"
            )
        }
    }

    private val appContext = context.applicationContext

    init {
        ensureChannel()
    }

    private fun ensureChannel() = ensureAlertChannel(appContext)

    /**
     * Posta un alert che squilla. Ritorna false quando soppresso (la sua chat
     * è a schermo), permesso mancante o errore — il chiamante Python logga e
     * basta, il messaggio resta comunque in chat.
     *
     * Soppresso solo se la chat è davvero davanti, non se l'app lo è: Jenny è
     * il launcher, e «in primo piano» voleva dire anche la pagina App o una
     * bacheca, dove il promemoria arrivava muto (v. MainActivity.isChatOnScreen).
     * «La sua chat» è quella del tag: la personale, o il quaderno di un cron del
     * quaderno ([AlertConversation.ofTag]).
     */
    fun postAlert(title: String, body: String, tag: String): Boolean {
        if (MainActivity.isInForeground && MainActivity.chatOnScreen(AlertConversation.ofTag(tag))) {
            Log.d(TAG, "Alert suppressed: the chat is on screen")
            return false
        }
        return post(title, body, tag)
    }

    /**
     * Come [postAlert], per la risposta a un messaggio scritto nell'app: tace
     * se l'app è davanti, su qualunque pagina.
     *
     * Il gate di [postAlert] (solo la chat personale a schermo) è giusto per un
     * avviso che parte da Jenny, e sbagliato per una risposta: chi ha appena
     * scritto la aspetta, e la vede arrivare. Dentro un quaderno, che ha una
     * chat sua, la chat personale non è mai a schermo — e dal 10/10/2026 ogni
     * risposta lì squillava, con l'utente che la stava leggendo.
     */
    fun postReply(title: String, body: String, tag: String): Boolean {
        if (MainActivity.isInForeground) {
            Log.d(TAG, "Reply alert suppressed: app in foreground")
            return false
        }
        return post(title, body, tag)
    }

    /**
     * Chiede all'utente se una richiesta dell'altra Jenny può far uscire
     * qualcosa dal telefono: «No» e «Consenti» (in quest'ordine, il sì a destra
     * come nei dialoghi di Android). Il tocco torna a Python da
     * `PeerActionReceiver` (`allow`/`deny` con *cid*).
     *
     * Nessun gate di visibilità, a differenza di [postAlert]: la domanda non è
     * in chat, quindi la chat a schermo non la mostra già.
     */
    fun postPeerConfirm(cid: String, title: String, body: String): Boolean =
        postPeer(
            PEER_KIND_CONFIRM, cid, title, body,
            listOf(
                peerAction(appContext, R.string.peer_deny_label,
                    PeerActionReceiver.ACTION_DENY, cid, needsUnlock = false),
                peerAction(appContext, R.string.peer_allow_label,
                    PeerActionReceiver.ACTION_ALLOW, cid, needsUnlock = true),
            ),
        )

    /**
     * Chiede il consenso alle mani di Jenny: «sta per usare WhatsApp». Stessa
     * notifica e stessa porta di [postPeerConfirm], con un tasto in più quando
     * *always* è vero — «Sempre», che non chiede più per quell'app. Per un
     * tasto che costa (paga, elimina) *always* è falso: lì si chiede ogni volta.
     */
    fun postPhoneAsk(cid: String, title: String, body: String, always: Boolean): Boolean =
        postPeer(
            PEER_KIND_CONFIRM, cid, title, body,
            buildList {
                add(peerAction(appContext, R.string.phone_no_label,
                    PeerActionReceiver.ACTION_DENY, cid, needsUnlock = false))
                if (always) {
                    add(peerAction(appContext, R.string.phone_always_label,
                        PeerActionReceiver.ACTION_ALWAYS, cid, needsUnlock = true))
                }
                add(peerAction(appContext, R.string.phone_ok_label,
                    PeerActionReceiver.ACTION_ALLOW, cid, needsUnlock = true))
            },
            // Con la pillola a schermo la domanda è già davanti all'utente: la
            // notifica resta nella tendina ma non scende sopra l'app. Sul
            // Titan, il 22/11/2026, copriva proprio la sveglia da eliminare.
            quiet = PhonePill.canShow(appContext),
        )

    /** Dice che Jenny sta usando il telefono, con «Ferma». Lo stesso *id*
     *  sostituisce la notifica; il tocco torna a Python come `stop`. Si toglie
     *  con [clearPeerNotice] (`done`). */
    fun postPhoneDriving(id: String, title: String, body: String): Boolean =
        postPeer(
            PEER_KIND_DONE, id, title, body,
            listOf(
                peerAction(appContext, R.string.phone_stop_label,
                    PeerActionReceiver.ACTION_STOP, id, needsUnlock = false),
            ),
            // Sempre silenziosa: è uno stato, con Stop, non una notizia. A
            // comparsa scendeva sopra l'app che Jenny stava usando, e lei
            // doveva scorrerla via per vedere (Titan, 23/11/2026).
            quiet = true,
        )

    /** Dice che una richiesta dell'altra Jenny è fatta, con «Annulla». Lo
     *  stesso *qid* sostituisce la notifica. Il tocco torna a Python da
     *  `PeerActionReceiver` (`undo` con *qid*). */
    fun postPeerDone(qid: String, title: String, body: String): Boolean =
        postPeer(
            PEER_KIND_DONE, qid, title, body,
            listOf(
                peerAction(appContext, R.string.peer_undo_label,
                    PeerActionReceiver.ACTION_UNDO, qid, needsUnlock = true),
            ),
        )

    /** La stessa notifica di [postPeerDone], ripostata dopo l'annullamento e
     *  senza «Annulla»: non c'è più niente da annullare, e il pulsante rimasto
     *  sotto «annullato» diceva il contrario (prova sui due emulatori,
     *  20/11/2026). */
    fun postPeerUndone(qid: String, title: String, body: String): Boolean =
        postPeer(PEER_KIND_DONE, qid, title, body, emptyList())

    /** Toglie la notifica di [postPeerConfirm] (*kind* `confirm`) o di
     *  [postPeerDone] (`done`). `false` su un tipo o un identificativo
     *  malformato. */
    fun clearPeerNotice(kind: String, ident: String): Boolean {
        if ((kind != PEER_KIND_CONFIRM && kind != PEER_KIND_DONE) ||
            !PeerActionReceiver.isValidIdent(ident)
        ) {
            Log.w(TAG, "Peer notice not cleared: bad kind or ident")
            return false
        }
        return try {
            cancelPeerNotice(appContext, kind, ident)
            true
        } catch (e: Exception) {
            Log.e(TAG, "Could not clear the peer notice", e)
            false
        }
    }

    private fun postPeer(
        kind: String,
        ident: String,
        title: String,
        body: String,
        actions: List<NotificationCompat.Action>,
        quiet: Boolean = false,
    ): Boolean {
        if (!PeerActionReceiver.isValidIdent(ident)) {
            Log.w(TAG, "Peer notice not posted: malformed ident")
            return false
        }
        if (!JennyChannels.canPostNotifications(appContext)) {
            Log.w(TAG, "Peer notice not posted: POST_NOTIFICATIONS is denied")
            return false
        }
        return try {
            val tag = peerTag(kind, ident)
            if (quiet) {
                JennyChannels.ensure(
                    appContext, QUIET_CHANNEL_ID, R.string.phone_channel_name,
                    R.string.phone_channel_description, NotificationManager.IMPORTANCE_LOW,
                    silent = true,
                )
            }
            val builder = NotificationCompat.Builder(appContext, if (quiet) QUIET_CHANNEL_ID else CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_jenny)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setPriority(if (quiet) NotificationCompat.PRIORITY_LOW else NotificationCompat.PRIORITY_HIGH)
                .setCategory(NotificationCompat.CATEGORY_MESSAGE)
                // Il tocco sul corpo apre la chat, ma la notifica resta: i suoi
                // pulsanti sono l'unico posto da cui si risponde.
                .setAutoCancel(false)
                // Ripostare lo stesso tag (un «fatto» aggiornato) non risuona.
                .setOnlyAlertOnce(true)
                .setContentIntent(
                    PendingIntent.getActivity(
                        appContext,
                        tag.hashCode(),
                        MainActivity.openChatIntent(appContext, null),
                        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                    )
                )
            actions.forEach { builder.addAction(it) }
            val manager = appContext.getSystemService(NotificationManager::class.java)
                ?: return false
            manager.notify(tag, PEER_NOTICE_ID, builder.build())
            Log.i(TAG, "Peer notice posted (kind=$kind)")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to post the peer notice", e)
            false
        }
    }

    private fun post(title: String, body: String, tag: String): Boolean {
        // Senza il permesso `notify` non solleva: scarta in silenzio, e a Python
        // tornava `true` per un avviso che l'utente non ha mai visto.
        if (!JennyChannels.canPostNotifications(appContext)) {
            Log.w(TAG, "Alert not posted: POST_NOTIFICATIONS is denied")
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
