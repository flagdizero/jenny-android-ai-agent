package com.flagdizero.jenny

import android.app.Activity
import android.util.Log
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import com.google.android.gms.auth.api.identity.Identity
import com.google.android.gms.tasks.Tasks
import java.util.concurrent.TimeUnit
import org.json.JSONArray
import org.json.JSONObject

/**
 * La parte nativa della copia su Google Drive che vuole un'Activity: il
 * consenso. Il resto (il token per il giro notturno, carica e Wi-Fi) è in
 * [DriveBridge], che Python chiama senza interfaccia.
 *
 * Un metodo solo, `connectDrive`, chiamato dalla WebUI con
 * `extensionCall("drive", "connectDrive")`. Se l'accesso c'è già la risposta è
 * immediata; se serve il consenso si apre la schermata di Google e l'esito
 * arriva dal launcher. In tutti i casi la pagina riceve
 * `window.jennyCloud.onDriveConnected("granted" | "denied" | "unavailable")`.
 */
object DriveExtension : NativeExtension {
    private const val TAG = "DriveExtension"
    private const val AUTHORIZE_TIMEOUT_S = 30L

    override val name = "drive"

    override fun attach(activity: MainActivity): NativeExtension.Attached = Attached(activity)

    private class Attached(private val activity: MainActivity) : NativeExtension.Attached {
        // La schermata di Google torna qui, e l'esito va alla pagina come stringa.
        private val consentLauncher: ActivityResultLauncher<IntentSenderRequest> =
            activity.registerForActivityResult(
                ActivityResultContracts.StartIntentSenderForResult()
            ) { result ->
                val granted = try {
                    result.resultCode == Activity.RESULT_OK &&
                        Identity.getAuthorizationClient(activity)
                            .getAuthorizationResultFromIntent(result.data).accessToken != null
                } catch (e: Exception) {
                    Log.i(TAG, "Drive consent not given (${e.javaClass.simpleName})")
                    false
                }
                answer(if (granted) "granted" else "denied")
            }

        override fun call(method: String, args: JSONArray) = when (method) {
            "connectDrive" -> connectDrive()
            else -> throw IllegalArgumentException("unknown drive command")
        }

        /**
         * Su un thread suo: l'attesa di Google arriva a 30 secondi, e sul
         * thread dei comandi nativi — uno solo — fermerebbe ogni altro comando
         * della pagina per tutto quel tempo.
         */
        private fun connectDrive() {
            Thread({
                if (!DriveBridge.isAvailable(activity)) {
                    answer("unavailable")
                    return@Thread
                }
                val result = try {
                    Tasks.await(DriveBridge.authorize(activity), AUTHORIZE_TIMEOUT_S, TimeUnit.SECONDS)
                } catch (e: Exception) {
                    Log.w(TAG, "Drive authorization failed (${e.javaClass.simpleName})")
                    answer("denied")
                    return@Thread
                }
                val pending = result.pendingIntent
                if (!result.hasResolution() || pending == null) {
                    answer(if (result.accessToken != null) "granted" else "denied")
                    return@Thread
                }
                activity.runOnUiThread {
                    if (activity.isDestroyed) return@runOnUiThread
                    consentLauncher.launch(IntentSenderRequest.Builder(pending.intentSender).build())
                }
            }, "jenny-drive-consent").start()
        }

        private fun answer(value: String) {
            activity.notifyJs("jennyCloud", "onDriveConnected", JSONObject.quote(value))
        }
    }
}
