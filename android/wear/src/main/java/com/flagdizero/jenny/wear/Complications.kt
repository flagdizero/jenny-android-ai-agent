package com.flagdizero.jenny.wear

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.util.Log
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.MonochromaticImage
import androidx.wear.watchface.complications.data.MonochromaticImageComplicationData
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.data.SmallImage
import androidx.wear.watchface.complications.data.SmallImageComplicationData
import androidx.wear.watchface.complications.data.SmallImageType
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceService
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
import androidx.wear.watchface.complications.datasource.ComplicationRequest

/**
 * Jenny sul quadrante: il tocco apre [AskActivity]. Due fonti perché i due
 * quadranti di jenny-watchface vogliono due illustrazioni diverse nello stesso
 * tipo (SMALL_IMAGE), e un quadrante sceglie il fornitore, non l'immagine.
 * Ricavate dalla sonda (my-data/jenny-wear-probe), dove sono state misurate.
 */
abstract class JennyComplication : ComplicationDataSourceService() {

    /** L'illustrazione intera, per lo slot SMALL_IMAGE. */
    abstract val picture: Int

    override fun onComplicationRequest(
        request: ComplicationRequest,
        listener: ComplicationRequestListener,
    ) {
        val tap = PendingIntent.getActivity(
            this, javaClass.name.hashCode(),
            Intent(this, AskActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        try {
            listener.onComplicationData(build(request.complicationType, tap))
        } catch (e: Exception) {
            Log.w(TAG, "${javaClass.simpleName}: invio fallito", e)
        }
    }

    override fun getPreviewData(type: ComplicationType): ComplicationData? = build(type, null)

    private fun build(type: ComplicationType, tap: PendingIntent?): ComplicationData? {
        val desc = text(getString(R.string.app_name))
        // jenny_mono: il bianco del disegno pieno, il nero trasparente. I
        // quadranti la tingono col loro colore.
        val mono = MonochromaticImage.Builder(Icon.createWithResource(this, R.drawable.jenny_mono)).build()
        return when (type) {
            // PHOTO e non ICON: è un disegno che riempie lo slot, non un
            // simbolo da ridimensionare con margine.
            ComplicationType.SMALL_IMAGE -> SmallImageComplicationData.Builder(
                SmallImage.Builder(Icon.createWithResource(this, picture), SmallImageType.PHOTO).build(), desc,
            ).setTapAction(tap).build()
            ComplicationType.MONOCHROMATIC_IMAGE ->
                MonochromaticImageComplicationData.Builder(mono, desc).setTapAction(tap).build()
            // I quadranti Google chiedono questa forma.
            ComplicationType.SHORT_TEXT -> ShortTextComplicationData.Builder(text("J"), desc)
                .setMonochromaticImage(mono).setTapAction(tap).build()
            else -> null
        }
    }

    companion object {
        private const val TAG = "JennyWear"
        private fun text(s: String) = PlainComplicationText.Builder(s).build()
    }
}

/** Jenny intera, per *Jenny · Protagonista* e per i quadranti di sistema. */
class JennyComplicationService : JennyComplication() {
    override val picture = R.drawable.jenny_face
}

/** Jenny che sbuca dal bordo in basso, per *Jenny · Al polso*. */
class PeekComplicationService : JennyComplication() {
    override val picture = R.drawable.jenny_sbuca
}

/**
 * Dopo ogni aggiornamento dell'app il quadrante tiene il vecchio tocco della
 * complicazione, che non porta più da nessuna parte finché non richiede i
 * dati: l'installazione ferma l'app e con lei il PendingIntent. Qui glieli si
 * fa richiedere subito, ed è misurato sulla sonda: rinfresca nello stesso
 * secondo.
 */
class UpdatedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        for (source in listOf(JennyComplicationService::class.java, PeekComplicationService::class.java)) {
            ComplicationDataSourceUpdateRequester.create(context, ComponentName(context, source))
                .requestUpdateAll()
        }
    }
}
