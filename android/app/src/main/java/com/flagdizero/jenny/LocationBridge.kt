package com.flagdizero.jenny

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Geocoder
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Bridge per la posizione del dispositivo, esposto a Python via Chaquopy
 * (`jclass("com.flagdizero.jenny.LocationBridge")`), mai istanziato da Kotlin —
 * stesso pattern di NotifierBridge / InstalledAppsBridge.
 *
 * Politica: qui vive solo l'accesso nativo (permesso, LocationManager,
 * Geocoder). La decisione "se e quando leggere la posizione" vive in Python
 * (jenny/runtime/location.py) ed è gattata dal toggle utente; questo bridge è
 * gattato dal permesso runtime `ACCESS_FINE_LOCATION` — senza permesso ogni
 * metodo ritorna `null` (mai un'eccezione verso Python).
 *
 * I metodi bloccano il thread chiamante: vanno invocati da un thread di lavoro
 * Python (`asyncio.to_thread`), mai dal main. Il formato di ritorno dei fix è
 * `"lat;lng;accuracy;timeMillis;provider"` — parsato da `_parse_fix`.
 */
@Suppress("DEPRECATION")
class LocationBridge(context: Context) {

    companion object {
        private const val TAG = "LocationBridge"

        /** La posizione è concessa, precisa o approssimativa: il bridge le
         *  accetta tutte e due. La chiede anche `MainActivity`, prima di
         *  aprire la richiesta del permesso. */
        fun hasPermission(context: Context): Boolean =
            ContextCompat.checkSelfPermission(
                context, Manifest.permission.ACCESS_FINE_LOCATION
            ) == PackageManager.PERMISSION_GRANTED ||
                ContextCompat.checkSelfPermission(
                    context, Manifest.permission.ACCESS_COARSE_LOCATION
                ) == PackageManager.PERMISSION_GRANTED

        /** Se il telefono sa cercare un posto per nome. Senza i servizi Google
         *  il Geocoder può mancare, e allora `find_place` non si registra
         *  (`geocoder_available` in jenny/runtime/location.py). */
        @JvmStatic
        fun isGeocoderPresent(): Boolean = try {
            Geocoder.isPresent()
        } catch (e: Exception) {
            false
        }
    }

    private val appContext = context.applicationContext
    private val lm = appContext.getSystemService(Context.LOCATION_SERVICE) as? LocationManager

    private fun hasPermission(): Boolean = hasPermission(appContext)

    private fun format(loc: Location?): String? {
        if (loc == null) return null
        val acc = if (loc.hasAccuracy()) loc.accuracy.toString() else ""
        return "${loc.latitude};${loc.longitude};$acc;${loc.time};${loc.provider ?: ""}"
    }

    /** Miglior fix last-known tra i provider abilitati (il più recente).
     *  Gratis: nessun radio acceso, solo la cache dell'OS. */
    fun getLastKnown(): String? {
        if (!hasPermission()) return null
        val manager = lm ?: return null
        return try {
            var best: Location? = null
            for (provider in manager.getProviders(true)) {
                val loc = try {
                    manager.getLastKnownLocation(provider)
                } catch (e: SecurityException) {
                    null
                } ?: continue
                if (best == null || loc.time > best.time) best = loc
            }
            format(best)
        } catch (e: Exception) {
            Log.w(TAG, "getLastKnown failed", e)
            null
        }
    }

    /** Un fix fresco entro `timeoutMs`, accendendo il provider migliore.
     *  Ricade sul last-known se scaduto o senza provider attivo. */
    fun getFresh(timeoutMs: Long): String? {
        if (!hasPermission()) return null
        val manager = lm ?: return null
        val provider = when {
            manager.isProviderEnabled(LocationManager.GPS_PROVIDER) -> LocationManager.GPS_PROVIDER
            manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
            else -> return getLastKnown()
        }
        return try {
            requestOne(manager, provider, timeoutMs)?.let(::format) ?: getLastKnown()
        } catch (e: SecurityException) {
            null
        } catch (e: Exception) {
            Log.w(TAG, "getFresh failed", e)
            getLastKnown()
        }
    }

    /**
     * Un fix fresco per la sentinella delle zone, **senza ripiego** sul
     * last-known: la sentinella deve sapere che la lettura è fallita (al
     * chiuso, senza permesso, a service cieco), non ricevere una posizione
     * vecchia spacciata per adesso.
     *
     * `lowPower`: prima la rete (wifi e celle), che costa pochissimo e basta per
     * una zona da 200 m; il GPS solo se la rete non c'è. Senza `lowPower`, il
     * GPS (poi la rete). `null` se nessun provider risponde entro `timeoutMs`.
     */
    fun getFreshFrom(lowPower: Boolean, timeoutMs: Long): String? {
        if (!hasPermission()) return null
        val manager = lm ?: return null
        val gps = manager.isProviderEnabled(LocationManager.GPS_PROVIDER)
        val net = manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        val provider = when {
            lowPower && net -> LocationManager.NETWORK_PROVIDER
            gps -> LocationManager.GPS_PROVIDER
            net -> LocationManager.NETWORK_PROVIDER
            else -> return null
        }
        return try {
            requestOne(manager, provider, timeoutMs)?.let(::format)
        } catch (e: Exception) {
            Log.w(TAG, "getFreshFrom($provider) failed: ${e.javaClass.simpleName}")
            null
        }
    }

    /**
     * Può la sentinella leggere la posizione a schermo spento?
     * `no_permission` senza permesso; `blind` col permesso ma col service senza
     * il tipo `location` (avviato dal background: v.
     * `GatewayService.locationTypeHeld`); `ok` altrimenti.
     */
    fun backgroundState(): String = when {
        !hasPermission() -> "no_permission"
        !GatewayService.locationTypeHeld -> "blind"
        else -> "ok"
    }

    /** Una sola lettura da *provider*, entro `timeoutMs`; `null` se non arriva.
     *  Solleva `SecurityException` se il permesso manca o l'app-op è negata. */
    private fun requestOne(manager: LocationManager, provider: String, timeoutMs: Long): Location? {
        val latch = CountDownLatch(1)
        val holder = arrayOfNulls<Location>(1)
        val listener = object : LocationListener {
            override fun onLocationChanged(location: Location) {
                holder[0] = location
                latch.countDown()
            }

            override fun onStatusChanged(p: String?, status: Int, extras: Bundle?) {}
            override fun onProviderEnabled(p: String) {}
            override fun onProviderDisabled(p: String) {
                latch.countDown()
            }
        }
        try {
            // Looper del main: il chiamante è un thread di lavoro Python senza
            // Looper proprio, e requestLocationUpdates ne pretende uno.
            manager.requestLocationUpdates(provider, 0L, 0f, listener, Looper.getMainLooper())
            val got = latch.await(timeoutMs, TimeUnit.MILLISECONDS)
            return if (got) holder[0] else null
        } finally {
            runCatching { manager.removeUpdates(listener) }
        }
    }

    /**
     * Cerca un posto per nome e indirizzo → JSON `[{"name","address","lat","lng"}]`
     * (parsato da `_parse_matches`), o `null`. Con un riquadro (i quattro lati non
     * tutti zero) cerca solo lì dentro: è il «qui vicino» di `find_places`.
     *
     * Non guarda il permesso di posizione, di proposito: cercare un indirizzo non
     * legge dov'è il telefono. Chiamato da un thread Python, non dal main.
     */
    fun findPlaces(
        query: String,
        max: Int,
        south: Double,
        west: Double,
        north: Double,
        east: Double
    ): String? {
        return try {
            if (!Geocoder.isPresent()) return null
            val geocoder = Geocoder(appContext, Locale.getDefault())
            val boxed = south != 0.0 || west != 0.0 || north != 0.0 || east != 0.0
            val results = (
                if (boxed) geocoder.getFromLocationName(query, max, south, west, north, east)
                else geocoder.getFromLocationName(query, max)
            ) ?: return null
            val out = JSONArray()
            for (a in results) {
                if (!a.hasLatitude() || !a.hasLongitude()) continue
                val street = listOfNotNull(a.thoroughfare, a.subThoroughfare).joinToString(" ")
                val city = listOfNotNull(a.postalCode, a.locality ?: a.subAdminArea).joinToString(" ")
                val address = listOf(street, city).filter { it.isNotBlank() }.joinToString(", ")
                    .ifBlank { a.getAddressLine(0) ?: "" }
                // `featureName` è il nome del posto quando ce l'ha; per un
                // indirizzo nudo è il numero civico, che sta già nell'indirizzo.
                val feature = a.featureName ?: ""
                val name = if (feature == a.subThoroughfare || feature == a.thoroughfare) "" else feature
                out.put(
                    JSONObject()
                        .put("name", name)
                        .put("address", address)
                        .put("lat", a.latitude)
                        .put("lng", a.longitude)
                )
            }
            out.toString()
        } catch (e: Exception) {
            Log.w(TAG, "findPlaces failed", e)
            null
        }
    }

    /** Reverse-geocoding sincrono → stringa leggibile (via, città, regione,
     *  paese). Chiamato da un thread Python, non dal main. */
    fun reverseGeocode(lat: Double, lng: Double): String? {
        return try {
            if (!Geocoder.isPresent()) return null
            val geocoder = Geocoder(appContext, Locale.getDefault())
            val results = geocoder.getFromLocation(lat, lng, 1) ?: return null
            if (results.isEmpty()) return null
            val a = results[0]
            val parts = listOfNotNull(
                a.thoroughfare,
                a.locality ?: a.subAdminArea,
                a.adminArea,
                a.countryName
            ).distinct()
            if (parts.isEmpty()) a.getAddressLine(0) else parts.joinToString(", ")
        } catch (e: Exception) {
            Log.w(TAG, "reverseGeocode failed", e)
            null
        }
    }
}
