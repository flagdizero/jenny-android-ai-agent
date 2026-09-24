package com.flagdizero.jenny

import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Il salto bloccante sul main thread che fanno i ponti chiamati da Python:
 * posta *block* sul main Looper, aspetta al massimo *timeoutMs* e ne ritorna
 * l'esito, o *fallback* se non arriva in tempo.
 *
 * Stava scritto quattro volte (`FloatingBridge.onMain` e tre metodi di
 * `JennyBrowserBridge`), ognuna con le sue idee. Qui una volta, con due regole
 * che prima non valevano ovunque:
 *
 * - **già sul main, lo si esegue sul posto**: un `post` seguito da un `await`
 *   sarebbe un blocco su sé stessi. Python chiama da un thread di lavoro, ma un
 *   deadlock è un guasto troppo silenzioso per affidarlo a un «dovrebbe»;
 * - **un'eccezione nel blocco si scrive nel log e vale *fallback***: nei metodi
 *   del browser il `post` non aveva un `try`, e un'eccezione sul main thread
 *   abbatteva il processo — gateway compreso.
 *
 * Fuori restano le attese che si sbloccano in una callback
 * (`evaluateJavascript`): lì il blocco finisce prima del risultato.
 */
object MainHop {

    fun <T> call(timeoutMs: Long, fallback: T, tag: String, block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        val result = AtomicReference(fallback)
        val done = CountDownLatch(1)
        Handler(Looper.getMainLooper()).post {
            try {
                result.set(block())
            } catch (e: Exception) {
                Log.e(tag, "Main thread call failed", e)
            } finally {
                done.countDown()
            }
        }
        return if (done.await(timeoutMs, TimeUnit.MILLISECONDS)) {
            result.get()
        } else {
            Log.i(tag, "Main thread call timed out after ${timeoutMs}ms")
            fallback
        }
    }
}
