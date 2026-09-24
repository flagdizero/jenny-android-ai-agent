package com.flagdizero.jenny

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Riavvia il gateway dopo un reboot del dispositivo (o un ripristino post
 * blackout): il telefono-server nel cassetto torna online senza intervento.
 *
 * Il tipo FGS `specialUse` non è tra quelli vietati all'avvio da
 * BOOT_COMPLETED, quindi startForegroundService è consentito qui.
 *
 * NIENTE `ACTION_LOCKED_BOOT_COMPLETED`, e non è una dimenticanza: quella
 * broadcast arriva PRIMA del primo sblocco, quando è montato solo lo storage
 * device-encrypted. Il gateway vive interamente in `filesDir`, che è storage
 * credential-encrypted (workspace, config.json, gli asset Python di Chaquopy):
 * prima dello sblocco quella cartella non è semplicemente vuota, è
 * inaccessibile, e l'avvio fallirebbe comunque. Servirebbe portare tutto in
 * `createDeviceProtectedStorageContext()` — cioè tenere chiavi API e memoria
 * fuori dalla cifratura legata al PIN, che non è un compromesso accettabile.
 * Dopo un reboot Jenny riparte al primo sblocco: è il comportamento voluto.
 *
 * `BOOT_COMPLETED` NON SIGNIFICA "IL TELEFONO SI È APPENA ACCESO", e chi legge
 * un log a mezzanotte lo darà per scontato: da Android 15 il sistema manda
 * questa broadcast anche a un'app che ESCE DALLO STATO *STOPPED* — cioè appena
 * aggiornata, appena installata o appena "force stop"-ata — la prima volta che
 * viene riavviata. Verificato sul Titan 2 (Android 16) il 2026-08-09: due
 * consegne, alle 01:52:14 e alle 02:05:09, con il telefono acceso ininterrot-
 * tamente da 34 giorni; la seconda a 2 secondi da un force stop seguito da un
 * `am start`, e il log di sistema la registra come broadcast vera
 * (`BOOT_COMPLETED_BROADCAST_COMPLETION_LATENCY_REPORTED ... receiversSize:1`),
 * non come sticky riemessa. Non è una stranezza della ROM e non c'è niente da
 * filtrare: è il sistema che ci dice "eri fermo, le tue sveglie non ci sono
 * più, riprogrammale", ed è esattamente ciò che serve — un force stop azzera
 * l'AlarmManager per questa app tanto quanto un reboot. Per questo il log qui
 * sotto stampa l'azione ricevuta e parla di *ri-armo*, non di avvio del
 * telefono: l'unica lettura che regge in entrambi i casi.
 */
class BootReceiver : BroadcastReceiver() {

    companion object {
        /**
         * Le broadcast che devono rimettere in piedi il gateway.
         *
         * - `BOOT_COMPLETED`: il caso ovvio (avvio del telefono) più quello che
         *   non lo è — l'app che esce dallo stato *stopped* dopo un force stop o
         *   un aggiornamento, da Android 15. Vedi il KDoc della classe: sono lo
         *   stesso problema (AlarmManager azzerato) e vogliono la stessa cura.
         * - `MY_PACKAGE_REPLACED`: dopo un aggiornamento dell'APK il sistema
         *   ferma il processo e NON lo fa ripartire. Senza questa azione il
         *   gateway restava giù finché l'utente non riapriva l'app a mano —
         *   invisibile per chi usa il telefono come server headless, che si
         *   accorgeva del down solo quando Telegram smetteva di rispondere.
         * - `QUICKBOOT_POWERON` (AOSP e la variante HTC, che diversi firmware
         *   cinesi hanno ereditato): su quelle ROM il "fast boot" ripristina
         *   uno stato salvato e non emette mai `BOOT_COMPLETED`, quindi il solo
         *   filtro standard lascerebbe il gateway spento dopo l'accensione.
         */
        private val ACCEPTED_ACTIONS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            "android.intent.action.QUICKBOOT_POWERON",
            "com.htc.intent.action.QUICKBOOT_POWERON",
        )
    }

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action !in ACCEPTED_ACTIONS) {
            return
        }
        // L'azione la si stampa per esteso perché le quattro non sono
        // intercambiabili quando si legge un log a posteriori — e perché
        // `BOOT_COMPLETED` non vuol dire che il telefono si sia acceso (vedi il
        // KDoc della classe). Da qui in giù il senso è comunque uno solo:
        // ri-armare tutto e rimettere su il gateway.
        Log.i("Jenny", "Received $action: re-arming alarms and starting gateway service")
        // Prima del service, e fuori dal try: sia un reboot sia l'uscita dallo
        // stato stopped azzerano le sveglie registrate nell'AlarmManager, quindi
        // la catena del watchdog è morta per definizione a questo punto e
        // nessuno la ri-armerebbe se l'avvio del gateway fallisse — cioè proprio
        // nel caso in cui serve. Armarla qui costa una sveglia e non dipende da
        // Python.
        Watchdog.arm(context)
        // Stesso motivo, un piano sotto: l'AlarmManager è azzerato, quindi anche
        // l'ultima rete è morta per definizione, e va rimessa su qui perché
        // altrimenti non la ri-armerebbe nessuno finché Python non riesce a
        // partire — che è esattamente il caso che deve saper riparare. Senza il
        // permesso sulle sveglie esatte `arm` degrada su una sveglia inesatta
        // invece di fallire (vedi il `catch` di `PowerBridge.scheduleAlarmClock`:
        // `setAlarmClock` quel permesso lo pretende eccome, verificato sul
        // dispositivo), quindi la rete resta, più lenta ma viva. `arm` legge il
        // flag dalle prefs e si disarma da sé se l'utente l'ha spenta.
        AlarmClockFallback.arm(context)
        GatewayStarter.ensureUp(context, reason = "boot/$action")
    }
}
