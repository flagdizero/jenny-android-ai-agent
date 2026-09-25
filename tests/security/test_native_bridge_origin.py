"""Il ponte nativo risponde alla SPA, non a ogni frame della WebView.

Prima c'era un oggetto solo, ``JennyNative``, installato con
``addJavascriptInterface``. Android lo inietta in **ogni** frame, qualunque sia
l'origine, e la WebView principale ospita due tipi di pagine non nostre: le
cornici delle Jenny App (``sandbox="allow-scripts"``) e la vista esterna, cioè
l'HTML del server dell'utente arrivato in chiaro dal proxy. Una qualunque di
quelle poteva chiamare ``saveToDownloads('config.json')`` — le chiavi dei
provider nella cartella Download condivisa — ``restartApp()``, o leggere il
conteggio d'uso del cassetto.

Il Kotlin non gira in CI, quindi il contratto si fissa sul sorgente ridotto al
solo codice (``support.kotlin_source``): un'annotazione rimessa per comodità su
un metodo che scrive riaprirebbe il buco senza che nessun altro test se ne
accorga. Lo shim JS invece si esegue davvero sotto node.
"""

from __future__ import annotations

import re
from pathlib import Path

from support.js_harness import requires_node, run_js
from support.kotlin_source import ANDROID_SRC, block_after, function_body, read_code

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
SHIM = UI / "assets" / "shared" / "native-bridge.js"

# I metodi che un iframe qualunque può chiamare senza danno: nessun effetto,
# nessun dato dell'utente. Allargare questo insieme è una decisione di
# sicurezza, non una comodità — v. il commento sopra installNativeBridges().
HARMLESS_READS = {
    "getBottomGestureInset",
    "hasHardwareKeyboard",
    "isBatteryExempt",
    "systemUpdatedSinceLastRun",
    "deviceManufacturer",
}

# Quelli per cui il difetto esisteva: devono stare dietro l'origine.
MUST_BE_ORIGIN_BOUND = {
    "saveToDownloads",
    "openFile",
    "shareFile",
    "exportBackup",
    "importBackup",
    "restartApp",
    "getLauncherUsage",
    "setLauncherUsage",
    "chatOpened",
}


def _main() -> str:
    return read_code("MainActivity")


def _raw_main() -> str:
    return (ANDROID_SRC / "MainActivity.kt").read_text(encoding="utf-8")


def _js_list(name: str) -> set[str]:
    src = SHIM.read_text(encoding="utf-8")
    m = re.search(rf"const {name} = \[(.*?)\];", src, re.S)
    assert m, f"{name} non trovato in native-bridge.js"
    return set(re.findall(r"'(\w+)'", m.group(1)))


def _annotated_methods(code: str) -> set[str]:
    return set(re.findall(r"@JavascriptInterface\s+fun\s+(\w+)", code))


def _dispatched(code: str) -> set[str]:
    body = function_body(code, "dispatch")
    # code_only svuota le stringhe: i nomi si leggono dal sorgente vero, nella
    # stessa porzione di file.
    raw = _raw_main()
    start = raw.index("fun dispatch(method: String")
    raw_body = raw[start : start + len(body) + 200]
    return set(re.findall(r'^\s*"(\w+)" ->', raw_body, re.M))


# ── Kotlin: cosa vede un iframe ──────────────────────────────────────────────


def test_only_harmless_reads_carry_javascript_interface() -> None:
    """Ogni `@JavascriptInterface` è una porta aperta a ogni frame."""
    assert _annotated_methods(_main()) == HARMLESS_READS


def test_the_sensitive_methods_are_not_reachable_from_any_frame() -> None:
    annotated = _annotated_methods(_main())
    assert not (annotated & MUST_BE_ORIGIN_BOUND), annotated & MUST_BE_ORIGIN_BOUND
    assert MUST_BE_ORIGIN_BOUND <= _dispatched(_main())


def test_the_all_frames_object_is_the_read_only_one() -> None:
    code = _main()
    calls = re.findall(r"addJavascriptInterface\((.*)\)", code)
    assert calls == ["JennyNativeInfo(), NATIVE_INFO_JS"], calls


def test_commands_are_bound_to_the_gateway_origin() -> None:
    code = _main()
    install = function_body(code, "installNativeBridges")
    assert re.search(
        r"WebViewCompat\.addWebMessageListener\(\s*wv,\s*NATIVE_PORT_JS,\s*"
        r"setOf\(GATEWAY_ORIGIN\),\s*NativeCommandListener\(\)\s*\)",
        install,
    ), install
    # Senza il supporto non si ripiega sulla porta aperta a tutti.
    assert install.count("addJavascriptInterface") == 1
    raw = _raw_main()
    assert 'GATEWAY_ORIGIN = "http://${GATEWAY_HOST}:${GATEWAY_PORT}"' in raw
    assert 'GATEWAY_HOST = "127.0.0.1"' in raw
    assert "GATEWAY_PORT = 18790" in raw


def test_the_listener_refuses_subframes_before_dispatching() -> None:
    """Chromium filtra per origine; il frame principale lo esige il Kotlin.

    Una cornice della stessa origine del gateway dentro la SPA — oggi non ce ne
    sono, ma una pagina di anteprima lo diventerebbe — non ha motivo di
    parlare col nativo.
    """
    code = _main()
    listener = block_after(code, r"inner class NativeCommandListener")
    guard = listener.index("if (!isMainFrame || !isGatewayOrigin(sourceOrigin))")
    assert listener.index("return", guard) < listener.index("nativeCommands.dispatch(")


def test_the_dispatch_is_closed() -> None:
    body = function_body(_main(), "dispatch")
    assert "else -> throw IllegalArgumentException" in body


def test_the_sensitive_bodies_left_the_all_frames_class() -> None:
    info = block_after(_main(), r"inner class JennyNativeInfo")
    for name in MUST_BE_ORIGIN_BOUND:
        assert f"fun {name}(" not in info, name


def test_the_system_update_read_has_no_side_effect() -> None:
    """Stava nel getter: un iframe che lo chiamava per primo consumava l'avviso."""
    info = block_after(_main(), r"inner class JennyNativeInfo")
    getter = info[info.index("fun systemUpdatedSinceLastRun()") :]
    getter = getter[: getter.index("\n")]
    assert "systemUpdateLatch == true" in getter
    latch = function_body(_main(), "latchSystemUpdate")
    assert "PREF_LAST_FINGERPRINT" in _raw_main()
    assert "putString(" in latch
    wait = function_body(_main(), "waitForGatewayThenLoad")
    assert wait.index("latchSystemUpdate()") < wait.index("loadWebView()")


# ── Kotlin ↔ JS: i due elenchi sono lo stesso elenco ──────────────────────────


def test_the_shim_mirrors_the_kotlin_surface() -> None:
    code = _main()
    assert _js_list("READS") == _annotated_methods(code)
    assert _js_list("COMMANDS") | _js_list("QUERIES") == _dispatched(code)
    assert not (_js_list("COMMANDS") & _js_list("QUERIES"))


def test_the_queries_are_the_methods_that_return_a_value() -> None:
    """Un comando che il JS spara e dimentica ma che il Kotlin fa rispondere, o
    viceversa, è un chiamante che aspetta per sempre o un valore buttato."""
    raw = _raw_main()
    start = raw.index("fun dispatch(method: String")
    body = raw[start : raw.index("else -> throw", start)]
    returning = {
        name for name, tail in re.findall(r'"(\w+)" ->(.*?)(?="\w+" ->|\Z)', body, re.S)
        if ".let { null }" not in tail
    }
    assert returning == _js_list("QUERIES")


def test_the_shells_load_the_shim_before_any_module() -> None:
    for shell in ("index.html", "workshop.html"):
        html = (UI / shell).read_text(encoding="utf-8")
        tag = '<script src="/html-mobile/assets/shared/native-bridge.js"></script>'
        assert tag in html, shell
        first_module = html.index('type="module"')
        assert html.index(tag) < first_module, shell
        assert html.index("bootstrap.js") < html.index(tag), shell
    manifest = (ROOT / "jenny" / "utils" / "android_assets.py").read_text(encoding="utf-8")
    assert '"assets/shared/native-bridge.js"' in manifest


def test_the_async_queries_are_awaited_by_their_callers() -> None:
    assets = UI / "assets"
    workspace = (assets / "mobile-workspace.js").read_text(encoding="utf-8")
    for call in ("await bridge.openFile(", "await bridge.shareFile(",
                 "await bridge.saveToDownloads("):
        assert call in workspace, call
    chat = (assets / "mobile-chat.js").read_text(encoding="utf-8")
    assert "await bridge.openFile(" in chat
    settings = (assets / "mobile-settings.js").read_text(encoding="utf-8")
    assert "await native.requestExactAlarmPermission()" in settings
    assert "await native.openBatterySettings()" in settings


# ── Lo shim, eseguito ────────────────────────────────────────────────────────


def _run_shim(prelude: str, script: str) -> str:
    source = (
        "import assert from 'node:assert/strict';\n"
        "const window = {};\n" + prelude + "\n"
        + SHIM.read_text(encoding="utf-8") + "\n" + script
    )
    return run_js(source)


@requires_node
def test_outside_the_shell_there_is_no_bridge() -> None:
    out = _run_shim("", "assert.equal(window.JennyNative, undefined); console.log('ok');")
    assert "ok" in out


@requires_node
def test_a_frame_with_only_the_read_port_gets_only_reads() -> None:
    """È ciò che vede una cornice di Jenny App: la porta dei comandi Chromium
    non ce la inietta, quindi di scrivere non c'è nemmeno il nome."""
    out = _run_shim(
        """
window.JennyNativeInfo = {
  getBottomGestureInset: () => 42, hasHardwareKeyboard: () => true,
  isBatteryExempt: () => false, systemUpdatedSinceLastRun: () => false,
  deviceManufacturer: () => 'Unihertz',
};""",
        """
const n = window.JennyNative;
assert.equal(n.getBottomGestureInset(), 42);
assert.equal(n.deviceManufacturer(), 'Unihertz');
for (const m of ['saveToDownloads', 'restartApp', 'getLauncherUsage', 'exportBackup', 'chatOpened']) {
  assert.equal(n[m], undefined, m);
}
console.log('ok');
""",
    )
    assert "ok" in out


@requires_node
def test_commands_post_and_queries_resolve_through_the_port() -> None:
    out = _run_shim(
        """
const posted = [];
let listener = null;
window.JennyNativePort = {
  postMessage: (s) => posted.push(JSON.parse(s)),
  addEventListener: (type, fn) => { if (type === 'message') listener = fn; },
};""",
        """
const n = window.JennyNative;
assert.equal(n.chatOpened(), undefined, 'un comando non torna niente');
assert.deepEqual(posted[0], { m: 'chatOpened', a: [] });
n.setGestureExclusion(1, 2, 3, 4);
assert.deepEqual(posted[1], { m: 'setGestureExclusion', a: [1, 2, 3, 4] });

const p = n.saveToDownloads('notes/a.md');
assert.ok(p instanceof Promise);
const req = posted[2];
assert.equal(req.m, 'saveToDownloads');
assert.deepEqual(req.a, ['notes/a.md']);
assert.ok(req.id > 0, 'una domanda porta un id');
// Una risposta per un altro id non la risolve.
listener({ data: JSON.stringify({ id: req.id + 99, ok: true, r: false }) });
listener({ data: JSON.stringify({ id: req.id, ok: true, r: true }) });
assert.equal(await p, true);

const q = n.getLauncherUsage();
listener({ data: JSON.stringify({ id: posted[3].id, ok: false }) });
await assert.rejects(q);
listener({ data: 'non json' });   // ignorato, non solleva
assert.equal(n.getBottomGestureInset, undefined, 'senza porta di lettura niente letture');
console.log('ok');
""",
    )
    assert "ok" in out
