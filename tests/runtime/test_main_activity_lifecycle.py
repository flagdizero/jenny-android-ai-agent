"""L'activity principale sopravvive a una ricreazione senza lasciare cocci.

Tre difetti dello stesso ciclo di vita:

- ``configChanges`` non dichiarava ``keyboard`` né ``navigation``: attaccare o
  staccare una tastiera Bluetooth ricreava l'activity, e con lei la SPA;
- ``onDestroy`` non distruggeva la WebView: l'istanza vecchia restava viva col
  suo renderer e la sua WebSocket accanto a quella nuova;
- ``pendingExportPath`` viveva solo in un campo: un'activity ricreata mentre il
  picker di salvataggio era davanti riceveva il risultato senza sapere cosa
  copiare, e rispondeva «annullato» a un backup confermato.

Il Kotlin non gira in CI: il contratto si fissa sul sorgente ridotto al solo
codice (``support.kotlin_source``). La lista completa di ``configChanges`` la
tiene ``tests/webui/test_native_shell_contract.py``.
"""

from __future__ import annotations

from support.kotlin_source import function_body, read_code


def _main() -> str:
    return read_code("MainActivity")


def test_on_destroy_destroys_the_webview_after_detaching_it() -> None:
    body = function_body(_main(), "onDestroy")
    assert "wv.destroy()" in body
    # Il campo si svuota PRIMA: i callback in ritardo leggono `webView?`.
    assert body.index("webView = null") < body.index("wv.destroy()")
    assert body.index("removeView(wv)") < body.index("wv.destroy()")
    # I comandi nativi smettono di entrare prima che la pagina sparisca.
    assert body.index("nativeExecutor.shutdown()") < body.index("wv.destroy()")
    assert body.index("wv.destroy()") < body.index("super.onDestroy()")


def test_a_pending_file_chooser_is_released_on_destroy() -> None:
    body = function_body(_main(), "onDestroy")
    assert "filePickerCallback?.onReceiveValue(null)" in body


def test_the_pending_export_survives_a_recreation() -> None:
    code = _main()
    save = function_body(code, "onSaveInstanceState")
    assert "outState.putString(STATE_PENDING_EXPORT" in save
    on_create = function_body(code, "onCreate")
    restore = on_create.index("pendingExportPath = savedInstanceState?.getString(STATE_PENDING_EXPORT)")
    # Prima di qualunque cosa possa consegnare il risultato del picker.
    assert restore < on_create.index("setContentView(")
