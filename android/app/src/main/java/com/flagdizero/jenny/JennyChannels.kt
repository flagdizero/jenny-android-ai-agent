package com.flagdizero.jenny

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat

/**
 * Le notifiche di Jenny: i canali, il permesso, il tocco che apre l'app.
 *
 * I quattro canali (il gateway, gli avvisi, il riavvio, gli aggiornamenti) li
 * costruiva a mano ciascuno il suo posto, e due posti guardavano il permesso e
 * due costruivano lo stesso `PendingIntent` verso [MainActivity].
 */
object JennyChannels {

    /**
     * Crea il canale [id] se non c'è (Android tiene nome e descrizione
     * aggiornati, e lascia stare l'importanza che l'utente ha cambiato).
     * [silent]: niente suono né vibrazione. Il `NotificationManager`, o `null`
     * se il sistema non lo dà.
     */
    fun ensure(
        ctx: Context,
        id: String,
        nameRes: Int,
        descriptionRes: Int,
        importance: Int,
        silent: Boolean = false,
    ): NotificationManager? {
        val manager = ctx.getSystemService(NotificationManager::class.java) ?: return null
        val channel = NotificationChannel(id, ctx.getString(nameRes), importance).apply {
            description = ctx.getString(descriptionRes)
            if (silent) {
                setSound(null, null)
                enableVibration(false)
            }
        }
        manager.createNotificationChannel(channel)
        return manager
    }

    /** Se l'app può mostrare una notifica: da Android 13 serve
     *  `POST_NOTIFICATIONS`, e senza `notify` la scarta in silenzio. */
    fun canPostNotifications(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    /**
     * Il tocco che apre Jenny, immutabile. Intent esplicito e senza categoria:
     * MainActivity è `singleTask` e riporta a galla il task esistente via
     * `onNewIntent`, che simula la pressione di Home (tornando in chat) solo
     * per gli intent con `CATEGORY_HOME`. Toccare lascia l'utente dov'era.
     */
    fun openJenny(ctx: Context, requestCode: Int): PendingIntent = PendingIntent.getActivity(
        ctx,
        requestCode,
        Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
}
