package com.flagdizero.jenny

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.util.Log
import com.google.android.gms.auth.GoogleAuthUtil
import com.google.android.gms.auth.api.identity.AuthorizationRequest
import com.google.android.gms.auth.api.identity.AuthorizationResult
import com.google.android.gms.auth.api.identity.Identity
import com.google.android.gms.common.ConnectionResult
import com.google.android.gms.common.GoogleApiAvailability
import com.google.android.gms.common.api.Scope
import com.google.android.gms.tasks.Task
import com.google.android.gms.tasks.Tasks
import java.util.concurrent.TimeUnit

/**
 * Accesso a Google Drive per il backup nel cloud, esposto a Python via Chaquopy
 * (`jclass("com.flagdizero.jenny.DriveBridge")`) — stesso pattern di PowerBridge.
 *
 * Lo scope è `drive.file`: Jenny vede solo i file che ha creato lei (la cartella
 * «Jenny»), niente del resto del Drive. Google lo classifica «non sensibile».
 *
 * Il consenso si dà una volta, da un'Activity (`DriveExtension.connectDrive`). Da
 * lì in poi [getToken] lo ottiene **senza interfaccia**, da qualunque thread non
 * principale: è ciò che rende possibile il giro notturno. Se Google chiede di
 * nuovo il consenso (permesso revocato, telefono nuovo) [getToken] torna `null`
 * e Python lo traduce in «serve il consenso» — mai un'eccezione attraverso il
 * confine Chaquopy.
 */
object DriveBridge {
    private const val TAG = "DriveBridge"
    const val SCOPE = "https://www.googleapis.com/auth/drive.file"
    private const val TIMEOUT_S = 30L

    fun request(): AuthorizationRequest =
        AuthorizationRequest.builder().setRequestedScopes(listOf(Scope(SCOPE))).build()

    /** Il compito di autorizzazione: con un'Activity può chiedere il consenso. */
    fun authorize(context: Context): Task<AuthorizationResult> =
        Identity.getAuthorizationClient(context).authorize(request())

    /** Google Play Services c'è e funziona: senza, il backup su Drive non si offre. */
    @JvmStatic
    fun isAvailable(context: Context): Boolean =
        GoogleApiAvailability.getInstance()
            .isGooglePlayServicesAvailable(context) == ConnectionResult.SUCCESS

    /** Il telefono è attaccato alla corrente (anche wireless). */
    @JvmStatic
    fun isCharging(context: Context): Boolean = try {
        val status = context.applicationContext.registerReceiver(
            null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)
        )
        (status?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
    } catch (e: Exception) {
        Log.w(TAG, "isCharging failed (${e.javaClass.simpleName})")
        false
    }

    /**
     * La rete attiva non è a consumo (Wi-Fi di casa, non i dati mobili): il giro
     * automatico non spende mai i giga dell'utente.
     */
    @JvmStatic
    fun isUnmetered(context: Context): Boolean = try {
        val cm = context.applicationContext.getSystemService(ConnectivityManager::class.java)
        val caps = cm?.getNetworkCapabilities(cm.activeNetwork)
        caps != null &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
    } catch (e: Exception) {
        Log.w(TAG, "isUnmetered failed (${e.javaClass.simpleName})")
        false
    }

    /**
     * Un token d'accesso per Drive, o `null` se serve il consenso dell'utente.
     *
     * [stale] è il token che Drive ha appena rifiutato (401): va tolto dalla
     * cache di Play Services, altrimenti `authorize` restituirebbe lo stesso.
     * Bloccante: si chiama da un thread di Python, mai dal main.
     */
    @JvmStatic
    fun getToken(context: Context, stale: String?): String? {
        if (context is Activity) Log.w(TAG, "getToken called with an Activity: no UI is shown")
        return try {
            if (!stale.isNullOrEmpty()) {
                try {
                    GoogleAuthUtil.clearToken(context.applicationContext, stale)
                } catch (e: Exception) {
                    Log.w(TAG, "clearToken failed (${e.javaClass.simpleName})")
                }
            }
            val result = Tasks.await(
                authorize(context.applicationContext), TIMEOUT_S, TimeUnit.SECONDS
            )
            if (result.hasResolution()) {
                Log.i(TAG, "Drive needs the user's consent")
                null
            } else {
                result.accessToken
            }
        } catch (e: Exception) {
            Log.w(TAG, "Drive token unavailable (${e.javaClass.simpleName}: ${e.message})")
            null
        }
    }
}
