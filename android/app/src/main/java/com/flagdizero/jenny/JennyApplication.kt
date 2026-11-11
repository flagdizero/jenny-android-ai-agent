package com.flagdizero.jenny

import android.app.Activity
import android.app.Application
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Bundle
import android.util.Log

/**
 * `Application` dell'app, e unica ragione per cui esiste: ospitare i due
 * trigger OPPORTUNISTICI che rimettono su il gateway.
 *
 * Perché non nel `GatewayService`: il service è proprio ciò che potrebbe non
 * esserci: registrare lì un ascoltatore che deve accorgersi che il service è
 * morto sarebbe un rilevatore di incendi alimentato dalla stanza che brucia.
 * L'`Application`, invece, esiste per definizione ogni volta che il processo
 * esiste — anche quando il processo è stato creato dal sistema solo per
 * consegnare una broadcast o eseguire un job.
 *
 * Nessuna `ensureUp` da `onCreate`: qui ci si passa a ogni creazione del
 * processo, comprese quelle in cui il sistema ci ha svegliati per un content
 * provider. Avviare un FGS da lì sarebbe un avvio da background non richiesto
 * da nessuno, per giunta nel contesto in cui Android 12+ lo rifiuta. I due
 * trigger sotto scattano invece su eventi che significano davvero "qualcosa è
 * cambiato, vale la pena ricontrollare".
 *
 * Entrambi confluiscono in `GatewayStarter`: nessuna `startForegroundService`
 * vive in questo file.
 *
 * Sugli stessi due eventi viaggia anche il ricontrollo del permesso sulle
 * sveglie esatte (`WakeReceiver.syncExactAlarmState`), e non su un terzo
 * ascoltatore suo: "qualcosa è cambiato, vale la pena ricontrollare" è
 * esattamente la stessa domanda, e l'utente che concede il permesso dalle
 * impostazioni di sistema torna su Jenny subito dopo.
 */
class JennyApplication : Application() {

    /** Ultimo controllo opportunistico, per il freno in `passesThrottle`. */
    private var lastTriggerMs = 0L

    override fun onCreate() {
        super.onCreate()
        registerNetworkTrigger()
        registerForegroundTrigger()
        // Il lavoro periodico si mette in coda da QUI e da nessun altro posto.
        // Due ragioni. La prima è di correttezza: l'auto-init di
        // `androidx.startup` gira in un ContentProvider, cioè PRIMA di questo
        // `onCreate`, quindi da qui `WorkManager.getInstance` è già servibile —
        // mentre più in basso (service, receiver) non si guadagna nulla. La
        // seconda è di copertura: questo metodo è l'unico che gira a ogni
        // creazione del processo, comunque sia stata provocata (launcher, boot,
        // broadcast, job), quindi una coda persa — cancellata dall'utente,
        // smontata da un "force stop" — si ricostruisce al primo risveglio
        // qualunque, senza dipendere dal fatto che il gateway riesca a partire.
        // Non è un `ensureUp`: mettere in coda un controllo fra 15 minuti non
        // avvia nessun foreground service, quindi non ricade nel divieto di
        // Android 12+ che è il motivo per cui `onCreate` non fa altro.
        GatewayWorker.ensureScheduled(this)
    }

    /**
     * Ritorno della rete → ricontrolla il gateway.
     *
     * Perché il caso conta: senza rete il gateway non parla col provider, i
     * turni falliscono e — su alcuni firmware — l'aereo prolungato è proprio il
     * momento in cui il gestore energetico decide che l'app non serve e la
     * congela. Il ritorno della connettività è quindi il segnale migliore che
     * abbiamo di "riprova adesso": costa zero e arriva prima di qualunque
     * scadenza periodica.
     *
     * `NetworkCallback` registrata a runtime e NON `CONNECTIVITY_ACTION` nel
     * manifest, e non è una preferenza stilistica: quella broadcast è deprecata
     * da API 28 e da **API 24 non viene più consegnata ai receiver dichiarati
     * nel manifest** (una delle restrizioni sulle broadcast implicite). Con
     * minSdk 26 un receiver di manifest per `CONNECTIVITY_ACTION` non
     * scatterebbe mai su nessun dispositivo supportato: sarebbe codice morto
     * che *sembra* una rete di sicurezza. La callback, in più, dice quale rete
     * è tornata e filtra da sé quelle senza Internet, invece di svegliarci a
     * ogni sfarfallio di stato.
     */
    private fun registerNetworkTrigger() {
        val manager = getSystemService(ConnectivityManager::class.java) ?: return
        // NET_CAPABILITY_INTERNET e basta: senza `NOT_METERED` né tipo di
        // trasporto, così il dato mobile vale quanto il Wi-Fi. Vogliamo sapere
        // che c'è *una* via verso Internet, non che sia quella buona.
        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        try {
            manager.registerNetworkCallback(
                request,
                object : ConnectivityManager.NetworkCallback() {
                    override fun onAvailable(network: Network) {
                        onOpportunisticTrigger("network-available")
                    }
                },
            )
        } catch (e: Exception) {
            // `registerNetworkCallback` ha un tetto per-app sulle callback e
            // solleva quando lo si sfonda. Qui ne registriamo una sola e per
            // sempre, ma se qualcosa andasse storto il gateway deve partire lo
            // stesso: questo è un trigger opportunistico, non un requisito.
            Log.e(TAG, "Could not register network callback", e)
        }
    }

    /**
     * App in primo piano → ricontrolla il gateway.
     *
     * È il momento in cui un utente che apre Jenny si aspetta che risponda: se
     * il gateway è giù, aspettare il prossimo giro del watchdog significa
     * mostrargli una WebView che non si connette per minuti.
     *
     * `ActivityLifecycleCallbacks` e non `ProcessLifecycleOwner`: quest'ultimo
     * vivrebbe in una dipendenza in più (`androidx.lifecycle:lifecycle-process`,
     * che `appcompat` NON si porta dietro) e il suo valore aggiunto — il debounce
     * da 700ms che evita un falso "background" quando un'activity ne apre
     * un'altra — qui non ha nulla da fare: l'app ha una sola activity, e
     * `MainActivity` assorbe nel manifest (`configChanges`) i cambi di
     * configurazione che capitano davvero, rotazione compresa, quindi non
     * viene ricreata. Non serve contare le
     * activity avviate, perché `onActivityStarted` scatta solo quando l'unica
     * activity torna visibile, che è esattamente l'evento che ci interessa.
     */
    private fun registerForegroundTrigger() {
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityStarted(activity: Activity) {
                recoverLocationType()
                onOpportunisticTrigger("app-foreground")
            }

            override fun onActivityCreated(activity: Activity, state: Bundle?) = Unit
            override fun onActivityResumed(activity: Activity) = Unit
            override fun onActivityPaused(activity: Activity) = Unit
            override fun onActivityStopped(activity: Activity) = Unit
            override fun onActivitySaveInstanceState(activity: Activity, out: Bundle) = Unit
            override fun onActivityDestroyed(activity: Activity) = Unit
        })
    }

    /**
     * Il service gira senza il tipo `location`: è partito dal background (boot,
     * aggiornamento, sveglia, watchdog), dove Android 14+ non concede la
     * posizione «mentre è in uso», e da allora ogni lettura a schermo spento
     * fallisce — le zone sono cieche (v. `GatewayService.locationTypeHeld`).
     * Adesso l'app è in primo piano: un nuovo `startForegroundService` porta
     * `startForegroundCompat` a richiedere il tipo da uno stato che lo ottiene.
     *
     * Fuori dal freno di `onOpportunisticTrigger`, e senza `ensureUpIfDown` (che
     * col gateway vivo non fa niente, ed era proprio il buco): costa un intent
     * solo quando il tipo manca davvero, e manca di rado.
     */
    private fun recoverLocationType() {
        if (!GatewayService.isRunning || GatewayService.locationTypeHeld) return
        if (!LocationBridge.hasPermission(this)) return
        GatewayStarter.ensureUp(this, reason = "recover-location")
    }

    private fun onOpportunisticTrigger(reason: String) {
        // PRIMA del freno, e di proposito. Il throttle esiste per non trasformare
        // una raffica di `onAvailable` in una raffica di `startForegroundService`;
        // questo controllo invece ha già il suo freno, molto più stretto: fa
        // qualcosa solo quando il permesso sulle sveglie esatte è DAVVERO
        // cambiato dall'ultima volta, che nella vita di un telefono succede una
        // manciata di volte in tutto. Metterlo dopo il freno significherebbe
        // perdere l'occasione buona — l'utente che concede il permesso e torna
        // subito su Jenny — solo perché la callback di rete ha parlato mezzo
        // minuto prima. Vedi `WakeReceiver.syncExactAlarmState`: la broadcast di
        // sistema che dovrebbe avvisarci non è arrivata sul dispositivo reale, e
        // questo è il giro che non dipende da lei.
        WakeReceiver.syncExactAlarmState(this, reason)
        if (!passesThrottle()) {
            return
        }
        // `alarmFallback = true`: nessuno dei due trigger gira dentro
        // l'allowlist temporanea che una sveglia concede, quindi su Android 12+
        // l'avvio del FGS da background può essere rifiutato. Il trigger da
        // foreground di solito no (c'è un'activity viva), ma la callback di rete
        // sì, e distinguere i due casi qui significherebbe indovinare.
        GatewayStarter.ensureUpIfDown(this, reason, alarmFallback = true)
    }

    /**
     * Un controllo al minuto al massimo, per l'insieme dei trigger.
     *
     * `onAvailable` non è un evento raro: scatta per ogni rete che soddisfa la
     * richiesta, quindi un passaggio Wi-Fi ↔ dati mobili, o un access point che
     * sfarfalla, ne producono una raffica. Senza freno ogni raffica diventa una
     * raffica di `startForegroundService`; con il freno resta un controllo. Il
     * throttle vive QUI e non in `GatewayStarter` di proposito: le reti
     * periodiche (watchdog, sveglia-sveglia, worker) hanno già il proprio ritmo
     * e non devono mai poter essere silenziate da un trigger opportunistico che
     * ha appena parlato.
     */
    @Synchronized
    private fun passesThrottle(): Boolean {
        val now = System.currentTimeMillis()
        val elapsed = now - lastTriggerMs
        // Orologio spostato indietro (fuso, sync NTP): `elapsed` negativo non è
        // "poco fa", è "non misurabile". Si riparte da adesso e si lascia
        // passare, che è il lato sicuro.
        if (elapsed in 0 until MIN_TRIGGER_INTERVAL_MS) {
            return false
        }
        lastTriggerMs = now
        return true
    }

    private companion object {
        const val TAG = "JennyApplication"
        const val MIN_TRIGGER_INTERVAL_MS = 60_000L
    }
}
