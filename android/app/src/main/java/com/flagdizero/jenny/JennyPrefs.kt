package com.flagdizero.jenny

import android.content.Context
import android.content.SharedPreferences

/**
 * Il file di preferenze dell'app: uno solo per tutti (attività, watchdog,
 * sveglie, rete d'emergenza), chiavi nuove e basta. Due file separati sarebbero
 * due lock e due punti da ricordare al momento di un wipe.
 *
 * Il nome lo legge anche Python, che vi trova la fotografia del battito
 * (`jenny/runtime/gap_history.py::PREFS_NAME`): prima stava scritto in quattro
 * file Kotlin, uno per lettore.
 */
object JennyPrefs {
    const val NAME = "jenny"

    fun of(context: Context): SharedPreferences =
        context.applicationContext.getSharedPreferences(NAME, Context.MODE_PRIVATE)
}
