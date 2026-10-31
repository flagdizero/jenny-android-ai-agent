package com.flagdizero.jenny

import org.json.JSONArray

/**
 * Un'estensione nativa: un pezzo del guscio che solo alcuni flavor contengono.
 *
 * Ogni flavor ha il suo `Extensions.kt` con l'elenco [Extensions.all]: vuoto in
 * `foss`, con la copia su Google Drive in `play`. Il codice di un'estensione
 * sta nel source set del suo flavor (`src/play/…`), quindi una build senza non
 * lo contiene affatto. È il gemello nativo di `jenny/extensions.py`, e il nome
 * di un'estensione è lo stesso nei tre posti (Python, WebUI, Kotlin).
 *
 * La WebUI la raggiunge con un solo comando generico del ponte,
 * `extensionCall(nome, metodo, …argomenti)` (v. [MainActivity.NativeCommands]):
 * così l'elenco chiuso dei comandi non nomina le estensioni che una build non
 * ha. Non serve chiedere quali ci sono: il JS di un'estensione arriva solo
 * dentro l'APK del flavor che ha anche la sua parte nativa.
 */
interface NativeExtension {
    /** Il nome, uguale a quello lato Python e WebUI. */
    val name: String

    /**
     * Aggancia l'estensione a un'Activity appena creata.
     *
     * Chiamata in `onCreate` subito dopo `super.onCreate`, **sempre** e sempre
     * nello stesso ordine — anche quando l'Activity se ne va subito: le chiavi
     * degli activity-result launcher registrati qui dipendono dall'ordine di
     * registrazione, e un risultato che torna dopo la morte del processo si
     * ritrova per quella chiave. L'oggetto restituito vive con quell'Activity
     * (una rotazione ne crea un altro): niente launcher in un `object`, che
     * tratterrebbe un'Activity morta.
     */
    fun attach(activity: MainActivity): Attached

    interface Attached {
        /**
         * Esegue [method] con [args], sul thread dei comandi nativi. È un
         * comando, senza risposta: chi deve dire un esito alla pagina lo fa con
         * [MainActivity.notifyJs]. Un metodo sconosciuto è un
         * `IllegalArgumentException`, come un comando sconosciuto del ponte.
         */
        fun call(method: String, args: JSONArray)
    }
}
