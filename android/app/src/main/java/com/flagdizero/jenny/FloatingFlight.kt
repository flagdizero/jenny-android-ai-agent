package com.flagdizero.jenny

import android.util.Log
import android.view.Choreographer
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sign
import kotlin.math.sin

/**
 * Il volo Pegman della mascotte flottante.
 *
 * **Non è fisica nuova.** È la stessa macchina a quattro fasi che
 * `jenny/templates/ui/assets/mobile-jenny.js` fa girare in `requestAnimationFrame`
 * per la mascotte in chat — dove il CSS la chiama già «Volo Pegman» — portata
 * qui perché la finestra overlay disegna con delle `View` e non con del DOM.
 *
 * Le costanti sono **copiate**, non ritarate: sono state scelte guardando la
 * mascotte muoversi, e un secondo insieme di numeri vorrebbe dire una mascotte
 * che oscilla in due modi diversi a seconda di dove la si guarda.
 * `tests/runtime/test_floating.py` confronta i due sorgenti perché quel giorno
 * diventi rosso invece che strano.
 *
 * L'unica traduzione è di unità: là sono px CSS, qui px del dispositivo. Tutto
 * ciò che è una **lunghezza** arriva già moltiplicato per la densità (il
 * chiamante passa `density`); rigidità, smorzamenti, rapporti di rimbalzo e
 * millisecondi sono adimensionali e si copiano tali e quali.
 *
 * ## Le quattro fasi
 *
 * `HELD` in mano, appesa al pivot e oscillante → `FALL` al rilascio, con la
 * gravità e le pareti morbide → `DOWN` a terra per il tempo di rialzarsi →
 * `SLIDE` a piedi fino al bordo, e lì finisce.
 */
class FloatingFlight(
    private val sizePx: Float,
    private val viewportW: Float,
    private val viewportH: Float,
    private val density: Float,
    /** Ordinata del pivot quando i piedi toccano il fondo dello schermo.
     *
     *  **Non e' piu' la riga sopra la barra di input.** Lo era nella prima
     *  stesura, copiata dalla SPA dove il dock *e'* il pavimento: sul telefono
     *  quella riga sta a un terzo di schermo dal fondo e senza niente di
     *  disegnato sotto, quindi si leggeva come un pavimento invisibile a
     *  mezz'aria. Qui il pavimento e' il fondo vero. */
    private val floorPivotY: Float,
    /** Ascissa del pivot agganciata, per lato: `(sinistra, destra)`. */
    private val dockPivotX: Pair<Float, Float>,
    private val onFrame: (left: Float, top: Float, rotationDeg: Float, pose: Pose, flip: Boolean) -> Unit,
    /** Atterrata: su quale bordo, e a quale ordinata del *riquadro*. */
    private val onSettled: (right: Boolean, top: Float) -> Unit,
) {

    enum class Pose { HANG, FALL, GROUND, WALK1, WALK2 }

    private enum class Phase { HELD, FALL, DOWN, SLIDE }

    companion object {
        // --- copiate da mobile-jenny.js, riga per riga ---
        /** La punta della manica alzata di `jenny-hang`, in frazioni del canvas. */
        const val PIVOT_X = 0.5083f
        const val PIVOT_Y = 0.4333f

        /** La molla con cui la mano la tiene: **sottosmorzata**, cioè elastica.
         *  È questa, non l'inseguimento del dito, a dare il penzolamento —
         *  lei resta indietro e recupera, e il pendolo ci oscilla sopra. */
        const val GRAB_K = 170f
        val GRAB_DAMP = (2.0 * Math.sqrt(GRAB_K.toDouble()) * 0.72).toFloat()

        const val G_L = 26f
        const val SWING_DAMP = 2.1f
        const val ACCEL_COUPLING = 0.0048f
        const val MAX_TILT_DEG = 78f
        const val WALL_REST = 0.42f
        const val FLOOR_REST = 0.42f

        /**
         * Il riquadro **pieno** dello sprite, in frazioni del quadrato.
         *
         * Misurato dal canale alfa dei `.webp` il 17/09/2026: il personaggio
         * sta in poco piu' di meta' canvas e il resto e' margine trasparente.
         * Senza questi numeri le pareti finiscono dove finisce il *file* e non
         * dove finisce *lei* — cioe' dei muri invisibili a un dito dal bordo,
         * che e' esattamente il difetto che chiudono.
         *
         * Sinistra, alto e destra vengono da `jenny-fall` (la posa che si vede
         * mentre vola); il basso da `jenny-ground`, che e' la posa con cui
         * tocca terra e quindi l'unica che decide dove sia il pavimento.
         */
        const val CONTENT_L = 0.2448f
        const val CONTENT_T = 0.1042f
        const val CONTENT_R = 0.7721f
        const val CONTENT_B = 0.9232f
        const val GETUP_MS = 700L
        const val WALK_FRAME_MS = 500L
        const val RETURN_TIMEOUT_MS = 6_000L

        // --- lunghezze: px CSS là, px dispositivo qui ---
        const val MAX_SPEED_CSS = 5_000f
        const val FALL_G_CSS = 1_300f
        const val WALK_SPEED_CSS = 150f
        const val DIR_MIN_CSS = 40f

        private const val MAX_DT = 0.033f
        private const val MIN_DT = 0.001f
    }

    private val maxTilt = Math.toRadians(MAX_TILT_DEG.toDouble()).toFloat()
    private val maxSpeed = MAX_SPEED_CSS * density
    private val fallG = FALL_G_CSS * density
    private val walkSpeed = WALK_SPEED_CSS * density
    private val dirMin = DIR_MIN_CSS * density

    /** Le pareti, in coordinate del pivot: lei le tocca **col personaggio**,
     *  non col bordo del file. */
    private val wallL = sizePx * (PIVOT_X - CONTENT_L)
    private val wallR = viewportW - sizePx * (CONTENT_R - PIVOT_X)
    private val wallT = sizePx * (PIVOT_Y - CONTENT_T)

    private var phase = Phase.HELD

    /** Dove sta il **dito**. La mascotte ci arriva per molla, non per copia. */
    private var fingerX = 0f
    private var fingerY = 0f

    /** Dove sta il **pivot** della mascotte (la punta della manica alzata). */
    private var px = 0f
    private var py = 0f
    private var vx = 0f
    private var vy = 0f
    private var th = 0f
    private var om = 0f
    private var axS = 0f
    private var dir = 1
    private var grounded = false
    private var downUntil = 0L
    private var deadline = 0L
    private var targetPx = 0f
    private var settled = false
    private var lastNanos = 0L
    private var running = false

    private val frames = Choreographer.FrameCallback { now -> onVsync(now) }

    /** In volo? Il chiamante lo guarda per sapere se la finestra deve restare grande. */
    val isFlying: Boolean get() = running

    /**
     * La prende in mano, alle coordinate **schermo** del pivot.
     *
     * Da qui in poi la posizione la detta il dito: `moveTo` ad ogni movimento.
     */
    fun grab(pivotX: Float, pivotY: Float) {
        phase = Phase.HELD
        fingerX = pivotX
        fingerY = pivotY
        px = pivotX
        py = pivotY
        vx = 0f
        vy = 0f
        th = 0f
        om = 0f
        axS = 0f
        grounded = false
        settled = false
        lastNanos = 0L
        if (!running) {
            running = true
            Choreographer.getInstance().postFrameCallback(frames)
        }
    }

    /**
     * Il dito si è spostato.
     *
     * Non sposta la mascotte: sposta **la mano**. Alla mascotte ci pensa la
     * molla, ed è per questo che quando strattoni lei arriva dopo.
     */
    fun moveTo(pivotX: Float, pivotY: Float) {
        if (phase != Phase.HELD) return
        fingerX = pivotX
        fingerY = pivotY
    }

    /** Il dito si alza: da qui comanda la gravità. */
    fun release(vxRelease: Float, vyRelease: Float) {
        if (phase != Phase.HELD) return
        vx = vxRelease.coerceIn(-maxSpeed, maxSpeed)
        vy = vyRelease.coerceIn(-maxSpeed, maxSpeed)
        setPhase(Phase.FALL)
        deadline = System.currentTimeMillis() + RETURN_TIMEOUT_MS
    }

    /** Ferma tutto senza consegnare nessun atterraggio. */
    fun cancel() {
        if (!running) return
        running = false
        Choreographer.getInstance().removeFrameCallback(frames)
    }

    private fun onVsync(nowNanos: Long) {
        if (!running) return
        val dt = if (lastNanos == 0L) MIN_DT else {
            ((nowNanos - lastNanos) / 1_000_000_000f).coerceIn(MIN_DT, MAX_DT)
        }
        lastNanos = nowNanos
        val nowMs = System.currentTimeMillis()

        if (phase == Phase.DOWN && nowMs >= downUntil) setPhase(Phase.SLIDE)
        step(dt, nowMs)
        // Toccato terra: da qui si sa dov'è caduta, quindi quale bordo le tocca.
        if (!settled && phase != Phase.FALL && phase != Phase.HELD) chooseSide()

        draw(nowMs)

        if (phase == Phase.SLIDE && (abs(px - targetPx) < 5f * density || nowMs >= deadline)) {
            settleNow()
            return
        }
        if (phase != Phase.HELD && phase != Phase.SLIDE && nowMs >= deadline) {
            // Failsafe: oltre il tetto si consegna lo stato finale invece di
            // restare a mezz'aria. Stessa rete di ``RETURN_TIMEOUT_MS`` in JS.
            if (!settled) chooseSide()
            settleNow()
            return
        }
        Choreographer.getInstance().postFrameCallback(frames)
    }

    private fun step(dt: Float, nowMs: Long) {
        var ax = 0f
        if (phase == Phase.HELD) {
            // La mano la tiene con una molla sottosmorzata: è il ritardo con
            // cui lei segue il dito, non un inseguimento rigido.
            ax = GRAB_K * (fingerX - px) - GRAB_DAMP * vx
            val ay = GRAB_K * (fingerY - py) - GRAB_DAMP * vy
            vx += ax * dt
            vy += ay * dt
        } else {
            ax = -1.7f * vx
            when (phase) {
                Phase.FALL -> {
                    vx -= 1.7f * vx * dt
                    if (py <= floorPivotY) {
                        vy += fallG * dt              // sopra il dock: cade
                    } else {
                        vy += (70f * (floorPivotY - py) - 17f * vy) * dt  // sotto: risale
                    }
                }
                Phase.DOWN -> {
                    // A terra dopo il tonfo: ferma, sta per rialzarsi.
                    vy = 0f
                    py = floorPivotY
                    vx -= 8f * vx * dt
                }
                else -> {
                    // Cammina a passo costante verso il bordo.
                    vy = 0f
                    py = floorPivotY
                    val d = targetPx - px
                    vx = if (abs(vx) > walkSpeed * 1.5f) {
                        vx - 6f * vx * dt                       // attrito residuo
                    } else {
                        sign(d) * min(walkSpeed, abs(d) / dt.coerceAtLeast(MIN_DT))
                    }
                }
            }
        }

        val speed = kotlin.math.hypot(vx, vy)
        if (speed > maxSpeed) {
            vx *= maxSpeed / speed
            vy *= maxSpeed / speed
        }
        px += vx * dt
        py += vy * dt

        // Pareti morbide: in mano e in caduta (lanciarla = rimbalza), ma NON in
        // cammino — il dock sta oltre il bordo dello schermo e le pareti le
        // impedirebbero di arrivare.
        if (phase != Phase.SLIDE) {
            if (px < wallL) { px = wallL; vx = abs(vx) * WALL_REST; om += vx * 0.002f }
            if (px > wallR) { px = wallR; vx = -abs(vx) * WALL_REST; om -= vx * 0.002f }
            if (py < wallT) { py = wallT; vy = abs(vy) * WALL_REST }
            // In basso **nessuna parete**: il pavimento e' piu' sotto e lo
            // gestisce il ramo FALL, con il suo rimbalzo e il suo tonfo. Una
            // parete qui gli ruberebbe l'urto — invertirebbe `vy` prima che il
            // pavimento lo veda — e lei non toccherebbe mai terra. In mano,
            // invece, il fondo la ferma: il dito non puo' spingerla sotto.
            if (phase == Phase.HELD && py > floorPivotY) { py = floorPivotY; vy = 0f }
        }

        // Il verso segue il moto, con isteresi.
        if (phase != Phase.DOWN && abs(vx) > dirMin) dir = if (vx > 0f) 1 else -1

        // Rimbalzo sulla quota del dock durante la caduta.
        if (phase == Phase.FALL) {
            if (py >= floorPivotY) grounded = true
            if (vy > 0f && py >= floorPivotY) {
                py = floorPivotY
                // Sotto una certa velocita' e' un tonfo secco; sopra
                // rimbalza per davvero. Il primo giro aveva `FLOOR_REST` a
                // 0,12 e un pavimento a mezz'aria: due ragioni perche' non si
                // vedesse mai un rimbalzo.
                if (abs(vy) < 500f * density) {
                    vy = 0f
                    setPhase(Phase.DOWN)
                    downUntil = nowMs + GETUP_MS
                } else {
                    vy = -abs(vy) * FLOOR_REST
                    om += vx * 0.0015f
                }
            } else if (abs(py - floorPivotY) < 3f * density && abs(vy) < 60f * density) {
                py = floorPivotY
                vy = 0f
                setPhase(Phase.DOWN)
                downUntil = nowMs + GETUP_MS
            }
        }

        // Il pendolo: la gravità raddrizza, l'accelerazione orizzontale fa
        // oscillare. Tre righe, e sono quelle in cui vive la sensazione.
        axS += (ax - axS) * min(1f, dt * 14f)
        val damp = when (phase) {
            Phase.HELD -> SWING_DAMP
            Phase.FALL -> SWING_DAMP * 2.2f
            else -> SWING_DAMP * 5f
        }
        val alpha = -G_L * sin(th) - ACCEL_COUPLING * axS * cos(th) - damp * om
        om += alpha * dt
        th += om * dt
        if (th > maxTilt) { th = maxTilt; om *= -0.35f }
        if (th < -maxTilt) { th = -maxTilt; om *= -0.35f }
    }

    /**
     * Sceglie il bordo di rientro dalla metà schermo in cui è atterrata, e da
     * lì la camminata ha una meta.
     */
    /** Ogni passaggio di fase si legge in `logcat`: senza, un volo che finisce
     *  troppo presto ha quattro spiegazioni indistinguibili. */
    private fun setPhase(next: Phase) {
        if (next == phase) return
        phase = next
        Log.i("FloatingOverlay", "flight phase=$next px=${px.toInt()} py=${py.toInt()} " +
            "vx=${vx.toInt()} vy=${vy.toInt()}")
    }

    /** Consegna l'atterraggio: bordo e ordinata del riquadro. */
    private fun settleNow() {
        running = false
        Choreographer.getInstance().removeFrameCallback(frames)
        onSettled(targetPx > viewportW / 2f, py - sizePx * PIVOT_Y)
    }

    private fun chooseSide() {
        settled = true
        val right = px >= viewportW / 2f
        targetPx = if (right) dockPivotX.second else dockPivotX.first
        deadline = System.currentTimeMillis() + RETURN_TIMEOUT_MS
    }

    private fun draw(nowMs: Long) {
        val left = px - sizePx * PIVOT_X
        val top = py - sizePx * PIVOT_Y
        if (phase == Phase.HELD) {
            // Appesa alla mano: ruota di −θ attorno al pivot. Nessun flip — in
            // mano l'arte resta nel suo verso, solo la caduta si specchia.
            onFrame(left, top, -Math.toDegrees(th.toDouble()).toFloat(), Pose.HANG, false)
            return
        }
        val pose = when {
            phase == Phase.SLIDE ->
                if ((nowMs / WALK_FRAME_MS) % 2L == 1L) Pose.WALK2 else Pose.WALK1
            phase == Phase.DOWN || grounded -> Pose.GROUND
            else -> Pose.FALL
        }
        onFrame(left, top, 0f, pose, dir < 0)
    }
}
