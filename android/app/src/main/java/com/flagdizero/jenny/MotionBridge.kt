package com.flagdizero.jenny

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorManager
import android.hardware.TriggerEvent
import android.hardware.TriggerEventListener
import android.util.Log
import com.chaquo.python.Python
import kotlin.concurrent.thread

/**
 * Il sensore di movimento significativo, per la sentinella delle zone
 * (`jenny/cron/zone_watch.py`): a telefono fermo la sentinella smette di
 * leggere la posizione e arma questo sensore, che la sveglia quando il telefono
 * riparte. Nessun permesso, nessuna libreria Google: è un sensore di sistema a
 * consumo quasi nullo, calcolato dal chip dei sensori anche a CPU addormentata.
 *
 * One-shot per natura (`requestTriggerSensor`): scattato, si disarma da solo, e
 * lo riarma Python al prossimo «fermo». Allo scatto il tick arriva a Python con
 * il codice `8003` (`power.WAKE_REQUEST_CODE_MOTION`), che sveglia solo la
 * sentinella. Costruito da Python via Chaquopy (`power._MOTION`), come gli
 * altri bridge; i metodi bloccano poco e si chiamano da un thread di lavoro.
 */
class MotionBridge(context: Context) {

    private val appContext = context.applicationContext
    private val manager = appContext.getSystemService(Context.SENSOR_SERVICE) as? SensorManager
    private val sensor: Sensor? = manager?.getDefaultSensor(Sensor.TYPE_SIGNIFICANT_MOTION)

    private val listener = object : TriggerEventListener() {
        override fun onTrigger(event: TriggerEvent?) {
            // Sul thread dei sensori: la consegna attraversa Chaquopy (JNI e GIL)
            // e può aspettare quanto un turno tiene il GIL, quindi su un thread
            // suo. Il lock di handoff tiene sveglia la CPU fino a Python, che poi
            // prende il suo (`keep_awake("zone")`) per la lettura.
            PowerBridge.acquireHandoffLock(appContext)
            thread(name = "jenny-motion") {
                try {
                    if (!Python.isStarted()) return@thread
                    val power = Python.getInstance().getModule("jenny.runtime.power")
                    val delivered = power.callAttr("on_wake_tick", REQUEST_CODE).toBoolean()
                    Log.i(TAG, "Motion tick delivered=$delivered")
                } catch (e: Exception) {
                    Log.e(TAG, "Motion tick delivery failed", e)
                } finally {
                    PowerBridge.releaseHandoffLock()
                }
            }
        }
    }

    /** Il telefono ha il sensore? Su qualche tablet e sugli emulatori vecchi no. */
    fun isAvailable(): Boolean = sensor != null

    /** Arma il sensore. `false` se non c'è o il sistema rifiuta. */
    fun arm(): Boolean {
        val m = manager ?: return false
        val s = sensor ?: return false
        return try {
            m.requestTriggerSensor(listener, s)
        } catch (e: Exception) {
            Log.w(TAG, "Motion sensor not armed: ${e.javaClass.simpleName}")
            false
        }
    }

    /** Disarma il sensore (nessuna zona, o il telefono si è mosso da sé). */
    fun disarm(): Boolean {
        val m = manager ?: return false
        val s = sensor ?: return false
        return try {
            m.cancelTriggerSensor(listener, s)
        } catch (e: Exception) {
            false
        }
    }

    companion object {
        private const val TAG = "Jenny"

        /** `power.WAKE_REQUEST_CODE_MOTION`: va alla sentinella delle zone. */
        const val REQUEST_CODE = 8003
    }
}
