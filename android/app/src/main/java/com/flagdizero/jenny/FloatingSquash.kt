package com.flagdizero.jenny

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.sin

/**
 * Lo schiaccia e allunga del volo della mascotte flottante.
 *
 * **Non sono formule nuove.** Sono quelle di
 * `jenny/templates/ui/assets/shared/mascot-squash.js`, che la mascotte in chat
 * applica allo stesso volo, portate qui riga per riga con le stesse costanti:
 * `tests/runtime/test_floating.py` confronta i due sorgenti — i nomi delle
 * costanti e i numeri dentro ogni funzione — perché due insiemi di numeri
 * vorrebbero dire una Jenny che si deforma in due modi diversi a seconda di
 * dove la si guarda.
 *
 * Ogni funzione ritorna una [Shape]: le due scale e uno spostamento verticale
 * in **px CSS** (il chiamante lo moltiplica per la densità). Le scale stanno
 * sempre sugli assi del disegno, mai lungo il verso del moto: in diagonale uno
 * sprite piatto si storce, e la testa scivola di lato rispetto ai piedi.
 */
object FloatingSquash {

    data class Shape(val sx: Float, val sy: Float, val dy: Float)

    val IDENTITY = Shape(1f, 1f, 0f)

    /** Appesa per la mano: si allunga quanto più la strattoni. */
    const val HELD_MAX = 0.05f
    const val HELD_SPEED = 4000f

    /** In aria: si allunga in verticale quanto va veloce su o giù. */
    const val AIR_MAX = 0.06f
    const val AIR_SPEED = 2500f

    /** Il tonfo: schiacciata quanto è caduta forte, poi una molla smorzata. */
    const val IMPACT_MIN = 0.05f
    const val IMPACT_MAX = 0.18f
    const val IMPACT_SPEED = 1800f
    const val IMPACT_PERIOD_MS = 300f
    const val IMPACT_DECAY_MS = 120f
    const val IMPACT_MS = 600f

    /** La rialzata: carica le gambe, si tira su, si assesta. */
    const val GETUP_LOAD_MS = 110f
    const val GETUP_RISE_MS = 150f
    const val GETUP_SETTLE_MS = 120f
    const val GETUP_MS = 380f

    /** Il passo: il periodo è quello dell'alternanza walk1/walk2. */
    const val STEP_MS = 500f
    const val STEP_LIFT_PX = 3f

    /** Dove poggiano i piedi, in frazioni del quadrato (`FEET` in JS). */
    const val FEET_GROUND = 0.92f
    const val FEET_WALK = 0.88f

    private fun stretch(s: Float, squeeze: Float): Shape {
        return Shape(1f - s * squeeze, 1f + s, 0f)
    }

    /** Appesa: *speed* è il modulo della velocità, in px CSS al secondo. */
    fun heldStretch(speed: Float): Shape {
        return stretch((abs(speed) / HELD_SPEED).coerceIn(0f, HELD_MAX), 0.45f)
    }

    /** In aria: *vy* è la velocità verticale, salita o caduta è uguale. */
    fun airStretch(vy: Float): Shape {
        return stretch((abs(vy) / AIR_SPEED).coerceIn(0f, AIR_MAX), 0.5f)
    }

    /** Il tonfo, *ms* dopo l'impatto a velocità *vImpact*. */
    fun impactSquash(vImpact: Float, ms: Float): Shape {
        if (ms < 0f || ms >= IMPACT_MS) return IDENTITY
        val amp = (abs(vImpact) / IMPACT_SPEED).coerceIn(IMPACT_MIN, IMPACT_MAX)
        val f = amp * cos((2f * PI.toFloat() * ms) / IMPACT_PERIOD_MS) * exp(-ms / IMPACT_DECAY_MS)
        return Shape(1f + f, 1f - f, 0f)
    }

    /** La rialzata, *ms* dopo la fine della pausa a terra. */
    fun getUp(ms: Float): Shape {
        if (ms < 0f || ms >= GETUP_MS) return IDENTITY
        if (ms < GETUP_LOAD_MS) {
            val k = ms / GETUP_LOAD_MS
            return Shape(1f + 0.06f * k, 1f - 0.07f * k, 0f)
        }
        if (ms < GETUP_LOAD_MS + GETUP_RISE_MS) {
            val k = (ms - GETUP_LOAD_MS) / GETUP_RISE_MS
            return Shape(1.06f - 0.1f * k, 0.93f + 0.12f * k, -4f * k)
        }
        val k = (ms - GETUP_LOAD_MS - GETUP_RISE_MS) / GETUP_SETTLE_MS
        return Shape(0.96f + 0.04f * k, 1.05f - 0.05f * k, -4f * (1f - k))
    }

    /** Il passo al tempo *now*, lo stesso orologio che sceglie walk1/walk2. */
    fun walkStep(now: Float): Shape {
        val p = (((now % STEP_MS) + STEP_MS) % STEP_MS) / STEP_MS
        val sy = 1f + 0.022f * sin(PI.toFloat() * p) - 0.035f * exp(-p * 10f)
        return Shape(1f - (sy - 1f) * 0.6f, sy, -STEP_LIFT_PX * sin(PI.toFloat() * p))
    }
}
