package com.flagdizero.jenny

import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.ResolveInfo
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import android.net.Uri
import android.provider.Settings
import android.util.Base64
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/**
 * Bridge exposing PackageManager's launchable-app list and launch capability
 * to Python, for the WebUI "App Android" tab. Resolved lazily via Chaquopy
 * (`jclass("com.flagdizero.jenny.InstalledAppsBridge")`), never instantiated
 * from Kotlin — same pattern as AgenticSearchBridge.
 */
class InstalledAppsBridge(context: Context) {

    companion object {
        private const val TAG = "InstalledAppsBridge"
        private const val ICON_SIZE_PX = 96
    }

    private val appContext = context.applicationContext

    private fun drawableToBase64Png(drawable: Drawable): String? {
        return try {
            val bitmap = if (drawable is BitmapDrawable && drawable.bitmap != null) {
                drawable.bitmap
            } else {
                val bmp = Bitmap.createBitmap(ICON_SIZE_PX, ICON_SIZE_PX, Bitmap.Config.ARGB_8888)
                val canvas = Canvas(bmp)
                drawable.setBounds(0, 0, ICON_SIZE_PX, ICON_SIZE_PX)
                drawable.draw(canvas)
                bmp
            }
            val stream = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)
            Base64.encodeToString(stream.toByteArray(), Base64.NO_WRAP)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to encode app icon", e)
            null
        }
    }

    @Suppress("DEPRECATION")
    fun listInstalledApps(): String {
        val pm = appContext.packageManager
        val launcherIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val resolved: List<ResolveInfo> = try {
            pm.queryIntentActivities(launcherIntent, 0)
        } catch (e: Exception) {
            Log.e(TAG, "queryIntentActivities failed", e)
            emptyList()
        }

        val apps = JSONArray()
        resolved
            .distinctBy { it.activityInfo.packageName }
            .sortedBy { it.loadLabel(pm).toString().lowercase() }
            .forEach { info ->
                apps.put(
                    JSONObject().apply {
                        put("packageName", info.activityInfo.packageName)
                        put("label", info.loadLabel(pm).toString())
                        val ai = info.activityInfo.applicationInfo
                        val isSystem = (ai.flags and ApplicationInfo.FLAG_SYSTEM) != 0 ||
                            (ai.flags and ApplicationInfo.FLAG_UPDATED_SYSTEM_APP) != 0
                        put("system", isSystem)
                        val icon = drawableToBase64Png(info.loadIcon(pm))
                        if (icon != null) put("icon", "data:image/png;base64,$icon")
                    }
                )
            }
        return apps.toString()
    }

    fun launchApp(packageName: String): Boolean {
        val pm = appContext.packageManager
        val intent = pm.getLaunchIntentForPackage(packageName) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            appContext.startActivity(intent)
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to launch $packageName", e)
            false
        }
    }

    /** Launch the system uninstall dialog for [packageName]. */
    fun uninstallApp(packageName: String): Boolean {
        val intent = Intent(Intent.ACTION_DELETE, Uri.parse("package:$packageName"))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            appContext.startActivity(intent)
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to uninstall $packageName", e)
            false
        }
    }

    /** Open the system "App info" settings screen for [packageName]. */
    fun openAppInfo(packageName: String): Boolean {
        val intent = Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:$packageName"),
        )
        // Un task suo, non quello di Impostazioni. Con il solo NEW_TASK l'affinita'
        // la faceva entrare nel task di Impostazioni gia' aperto, e Indietro
        // tornava alla schermata lasciata li' (sul Titan 2, «Rete e Internet»)
        // invece che a Jenny. NEW_DOCUMENT le da' un task per pacchetto: Indietro
        // lo chiude e torna a chi l'ha aperta, e riaprirla non ne accumula altri.
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NEW_DOCUMENT)
        return try {
            appContext.startActivity(intent)
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open app info for $packageName", e)
            false
        }
    }
}
