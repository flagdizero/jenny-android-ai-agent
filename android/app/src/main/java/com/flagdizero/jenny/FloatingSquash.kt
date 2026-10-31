package com.flagdizero.jenny

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.exp

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

    /** La ripartenza: carica le gambe da ferma negli ultimi [LOAD_MS] a terra,
     *  e si distende nel primo passo in [BLEND_MS]. Niente spostamento in su:
     *  alzarla mentre comincia a camminare si leggeva come un saltellino. */
    const val LOAD_MS = 110f
    const val BLEND_MS = 150f

    /** Il passo, una curva continua: il periodo è quello di walk1/walk2. */
    const val STEP_MS = 500f
    const val STEP_LIFT_PX = 2f

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

    /** Carica le gambe, *ms* dopo l'inizio della carica (a terra, ferma). */
    fun loadLegs(ms: Float): Shape {
        val k = (ms / LOAD_MS).coerceIn(0f, 1f)
        return Shape(1f + 0.06f * k, 1f - 0.07f * k, 0f)
    }

    /** Il passo al tempo *now*, lo stesso orologio che sceglie walk1/walk2:
     *  *d* vale 1 al cambio di piede e 0 a metà passo. */
    fun walkStep(now: Float): Shape {
        val p = (((now % STEP_MS) + STEP_MS) % STEP_MS) / STEP_MS
        val d = (1f + cos(2f * PI.toFloat() * p)) / 2f
        val sy = 1f + 0.02f * (1f - d) - 0.025f * d
        return Shape(1f - (sy - 1f) * 0.6f, sy, -STEP_LIFT_PX * (1f - d))
    }

    /** I primi passi, *ms* dopo la ripartenza: dalla fine di [loadLegs] si
     *  distende, e il passo entra con lo stesso peso. */
    fun walkStart(ms: Float, now: Float): Shape {
        val k = (ms / BLEND_MS).coerceIn(0f, 1f)
        val e = 1f - (1f - k) * (1f - k)
        val w = walkStep(now)
        return Shape(
            (1.06f - 0.06f * e) * (1f + (w.sx - 1f) * e),
            (0.93f + 0.07f * e) * (1f + (w.sy - 1f) * e),
            w.dy * e,
        )
    }
}
