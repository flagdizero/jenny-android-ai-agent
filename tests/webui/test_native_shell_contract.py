"""Il guscio nativo: cosa deve sopravvivere a un cambio di configurazione, chi
prende il tasto Indietro, quando si può chiedere un permesso e dove porta il tap
su una notifica.

Cinque difetti con la stessa forma: il codice nativo dava per scontato uno stato
che non c'è. L'activity dava per scontato che nessuna configurazione cambi
mentre è a schermo; la WebView dava per scontato che 100 sia "la dimensione
giusta" del testo; ``onCreate`` dava per scontato di poter chiedere due permessi
nello stesso giro; il callback del back dava per scontato che una SPA ci sia
sempre; la notifica proattiva dava per scontato che portare l'app in primo piano
equivalga a mostrare il messaggio.

Asserzioni sul sorgente, nello stile di ``test_back_navigation_contract.py``: non
c'è un emulatore in CI, e queste sono proprietà del codice, non del runtime.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android" / "app" / "src" / "main"
JAVA = ANDROID / "java" / "com" / "flagdizero" / "jenny"
MANIFEST = ANDROID / "AndroidManifest.xml"
MAIN_ACTIVITY = JAVA / "MainActivity.kt"
NOTIFIER = JAVA / "NotifierBridge.kt"
LAYOUT = ANDROID / "res" / "layout" / "activity_main.xml"
THEMES = ANDROID / "res" / "values" / "themes.xml"
UI_ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"


def _main_activity() -> str:
    return MAIN_ACTIVITY.read_text(encoding="utf-8")


def _app_js() -> str:
    return (UI_ASSETS / "mobile-app.js").read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  {name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato in mobile-app.js"
    return body.group(1)


def _fun_body(source: str, name: str) -> str:
    """Corpo di una funzione Kotlin, per bilanciamento di graffe.

    Basta per questo file: nessuna delle funzioni ispezionate contiene graffe
    spaiate dentro una stringa (i template ``${...}`` sono bilanciati per
    costruzione).
    """
    match = re.search(rf"\bfun {re.escape(name)}\s*\(", source)
    assert match, f"funzione {name} non trovata"
    start = source.index("{", match.end())
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : i]
    raise AssertionError(f"graffe non bilanciate in {name}")


def _code_only(source: str) -> str:
    """Via i commenti: qui si asserisce su cosa il codice *fa*, e più di un
    commento nomina apposta la riga che è stata tolta."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(line.split("//")[0] for line in without_block.splitlines())


def _activity_config_changes() -> set[str]:
    xml = MANIFEST.read_text(encoding="utf-8")
    activity = re.search(r"<activity\b.*?</activity>", xml, re.S)
    assert activity, "activity principale non trovata nel manifest"
    attr = re.search(r'android:configChanges="([^"]+)"', activity.group(0))
    assert attr, "l'activity principale non dichiara configChanges"
    return {token.strip() for token in attr.group(1).split("|")}


# ── #9 · configChanges ────────────────────────────────────────────────────────


def test_the_activity_absorbs_the_config_changes_that_really_happen() -> None:
    """Ogni configurazione non elencata ricrea l'activity, e ricreare l'activity
    distrugge la WebView — cioè la SPA, con la vista corrente, lo scroll della
    chat, la mini-app aperta e la connessione WebSocket. Non c'è nessun
    ``onSaveInstanceState`` che la rimetta a posto: ``stateNotNeeded="true"``
    dichiara proprio che lo stato non si salva.

    Mancavano i quattro cambi che su un telefono capitano davvero con l'app
    davanti: tema scuro di sistema (``uiMode``, anche quello automatico
    all'alba), dimensione carattere (``fontScale``), dimensione display
    (``density``, con i due compagni di viaggio ``smallestScreenSize`` e
    ``screenLayout``) e lingua (``locale``).
    """
    tokens = _activity_config_changes()
    for required in (
        "orientation",
        "screenSize",
        "smallestScreenSize",
        "screenLayout",
        "keyboardHidden",
        "uiMode",
        "fontScale",
        "density",
        "locale",
    ):
        assert required in tokens, f"configChanges senza {required}"


def test_absorbing_uimode_is_safe_because_nothing_native_follows_it() -> None:
    """Assorbire ``uiMode`` senza ricreare è sicuro solo se nessuna risorsa
    dipende dalla modalità notte. Se un domani ``Theme.Jenny`` diventasse
    ``DayNight``, o il CSS della WebUI iniziasse a usare ``prefers-color-scheme``,
    l'app resterebbe coi colori vecchi fino al riavvio — e nessuno collegherebbe
    la cosa a questa riga di manifest.
    """
    themes = THEMES.read_text(encoding="utf-8")
    assert "DayNight" not in themes
    non_vendor_css = [
        path
        for path in UI_ASSETS.rglob("*.css")
        if "vendor" not in path.parts and "prefers-color-scheme" in path.read_text(encoding="utf-8")
    ]
    assert non_vendor_css == [], f"prefers-color-scheme in {non_vendor_css}"


def test_absorbing_locale_is_safe_because_the_layout_has_no_string_resources() -> None:
    """Stessa condizione per ``locale``: il layout nativo scrive le sue tre
    stringhe in chiaro e non referenzia nessun ``@string``, quindi non c'è niente
    da ri-risolvere al cambio lingua. La WebUI ha la sua i18n, con selettore
    dedicato in Impostazioni.
    """
    assert "@string/" not in LAYOUT.read_text(encoding="utf-8")


# ── N12 · dimensione carattere ────────────────────────────────────────────────


def test_the_webview_keeps_the_system_font_size() -> None:
    """``textZoom`` non è lo zoom: è la dimensione carattere di sistema, che la
    WebView eredita apposta. Fissarla a 100 la annullava, e con il pinch-zoom
    disattivato (giustamente: la UI è un launcher) e nessun controllo di
    dimensione nella WebUI non restava **nessuna** accomodazione per chi ha
    vista ridotta.

    Il pinch resta disattivato: sono due impostazioni diverse e solo una delle
    due era motivata dal commento che le copriva entrambe.
    """
    kotlin = _main_activity()
    assert "textZoom" not in _code_only(_fun_body(kotlin, "loadWebView"))
    assert "setSupportZoom(false)" in kotlin


# ── N5 · permessi all'avvio ───────────────────────────────────────────────────


def test_the_two_startup_permissions_are_asked_one_after_the_other() -> None:
    """``Activity`` tiene una sola richiesta di permessi per volta
    (``mHasCurrentPermissionsRequest``): la seconda lanciata nello stesso giro
    del main thread viene scartata con "Can request only one set of permissions
    at a time". Chiedendoli entrambi di fila in ``onCreate``, al primo avvio il
    dialog della posizione non compariva **mai** — e siccome il codice non
    distingue "negato" da "mai chiesto", non ricompariva nemmeno dopo.

    Il contratto: ``onCreate`` avvia solo il primo anello; la posizione parte dal
    callback del launcher delle notifiche, in entrambi i rami (concesso o
    negato: la posizione va comunque chiesta).
    """
    kotlin = _main_activity()
    on_create = _code_only(_fun_body(kotlin, "onCreate"))
    assert "ensureNotificationPermission()" in on_create
    assert "ensureLocationPermission" not in on_create

    launcher = kotlin.split("private val notificationPermissionLauncher", 1)[1]
    launcher = _code_only(launcher.split("private val locationPermissionLauncher", 1)[0])
    assert "ensureLocationPermission()" in launcher, (
        "la posizione deve partire dal callback delle notifiche, non in parallelo"
    )
    # Il ramo negato non deve saltare la posizione: la chiamata sta fuori
    # dall'if/else, quindi una sola occorrenza copre entrambi i rami.
    assert launcher.count("ensureLocationPermission()") == 1

    # Chi non ha nulla da chiedere passa comunque il testimone.
    ensure_notification = _code_only(_fun_body(kotlin, "ensureNotificationPermission"))
    assert ensure_notification.count("ensureLocationPermission()") == 2, (
        "servono entrambe le uscite corte: SDK < 33 e permesso già concesso"
    )


# ── N24 · il back non esiste finché non esiste la SPA ─────────────────────────


def test_the_back_callback_lives_exactly_as_long_as_the_spa_on_screen() -> None:
    """Registrato abilitato da ``onCreate``, il callback intercettava il tasto
    Indietro anche quando non c'era nessuna SPA a cui darlo: per tutta la
    ripartenza del gateway (fino a ``BOOT_POLL_TIMEOUT_MS``, 90 s) e **per
    sempre** sulla schermata d'errore, dove l'unico comando è ``retry_button``.
    Un tasto morto che tiene occupata la pressione senza lasciarla a nessuno.
    """
    kotlin = _main_activity()
    assert "OnBackPressedCallback(false)" in kotlin
    assert "OnBackPressedCallback(true)" not in kotlin
    assert "backCallback?.isEnabled = true" in _code_only(_fun_body(kotlin, "onPageFinished"))
    assert "backCallback?.isEnabled = false" in _code_only(_fun_body(kotlin, "showLoading"))
    assert "backCallback?.isEnabled = false" in _code_only(_fun_body(kotlin, "showError"))


def test_the_back_callback_cannot_flip_back_and_forth() -> None:
    """Abilitare in ``onPageFinished`` e disabilitare in ``showLoading`` sarebbe
    un'altalena se i due potessero alternarsi sulla stessa pagina: la SPA fa
    navigazioni di main frame (ricarica di recupero) e ognuna passa da
    ``onPageStarted``.

    Non succede grazie alle due guardie che esistono già: ``onPageStarted``
    mostra il loading solo ``if (!loaded)`` e ``onPageFinished`` esce subito
    ``if (loaded)``. A rimettere ``loaded = false`` è solo il pulsante Riprova,
    che passa proprio da ``showLoading()``.
    """
    kotlin = _main_activity()
    started = _code_only(_fun_body(kotlin, "onPageStarted"))
    assert re.search(r"if\s*\(!loaded\)\s*showLoading\(\)", started)
    finished = _code_only(_fun_body(kotlin, "onPageFinished"))
    assert re.search(r"if\s*\(loaded\)\s*return", finished)


# ── #24 · il tap sulla notifica proattiva ─────────────────────────────────────


def test_the_alert_notification_carries_a_routable_action() -> None:
    """Il ``contentIntent`` era muto: un ``Intent`` verso ``MainActivity`` senza
    action, indistinguibile da un rilancio qualunque. ``onNewIntent`` instrada
    solo ``CATEGORY_HOME``, quindi il tap riportava l'app in primo piano
    esattamente dov'era — dentro una mini-app, in Wiki, ovunque — e il messaggio
    proattivo non veniva mostrato.
    """
    notifier = NOTIFIER.read_text(encoding="utf-8")
    assert "setAction(MainActivity.ACTION_OPEN_CHAT)" in notifier
    kotlin = _main_activity()
    assert re.search(r"\bconst val ACTION_OPEN_CHAT\b", kotlin), (
        "l'action deve essere pubblica: NotifierBridge la legge da qui"
    )


def test_tapping_the_alert_closes_what_is_above_and_lands_in_chat() -> None:
    """Il ramo deve smontare i livelli sopra la vista e *forzare* la chat — la
    vista "home" è una preferenza e può non esserlo, mentre il messaggio che
    l'utente ha toccato sta in chat.

    Il guscio non compone più il comportamento da sé. ``goHome()`` seguito da
    ``switchMode('chat', false)`` lasciava la entry di radice a descrivere la
    *vista home* mentre a schermo c'era la chat: con una vista home diversa da
    chat, il primo Indietro atterrava dove l'utente non era mai stato. La SPA
    espone ``openChat()``, che è il comportamento intero in un punto solo.
    """
    kotlin = _main_activity()
    on_new_intent = _code_only(_fun_body(kotlin, "onNewIntent"))
    assert "ACTION_OPEN_CHAT" in on_new_intent
    assert "OPEN_CHAT_JS" in on_new_intent
    open_chat_js = re.search(r"OPEN_CHAT_JS = \"\"\"(.*?)\"\"\"", kotlin, re.S)
    assert open_chat_js, "OPEN_CHAT_JS non trovato"
    body = open_chat_js.group(1)
    assert "app.openChat()" in body
    assert "goHome()" not in body, "il guscio non ricompone il comportamento a mano"

    # E il lato SPA deve davvero fare le tre cose, non solo esistere.
    open_chat = _method(_app_js(), "openChat")
    assert "this._dismissAllOverlays()" in open_chat
    assert "collapseToRoot?.()" in open_chat
    assert "this._navPos = 0" in open_chat, "la chat diventa la radice, non una entry sopra"


def test_pending_alerts_are_cleared_where_the_chat_reaches_the_screen() -> None:
    """La regola è "la chat è a schermo", e i tre chiamanti sono i tre modi in cui
    ci arriva: il tap sull'alert (``onNewIntent`` + il gemello in ``onCreate``
    per l'activity morta), il cambio vista dentro la SPA (``chatOpened``) e il
    rientro in primo piano a chat già attiva (``onResume``).

    È stata sbagliata in due modi opposti, e questo test tiene entrambe le
    sponde. In ``onResume`` liscio: questa app è la home del telefono, quindi
    l'alert veniva cancellato a ogni pressione di Home, letto o no. Solo sul tap
    dell'alert: chi apriva la chat da sé restava con in coda notifiche di
    messaggi che aveva davanti agli occhi.
    """
    kotlin = _main_activity()
    assert "clearAlerts" in _code_only(_fun_body(kotlin, "onNewIntent"))
    assert "clearAlerts" in _code_only(_fun_body(kotlin, "onCreate"))
    assert "clearAlerts" in _code_only(_fun_body(kotlin, "chatOpened"))
    assert "clearAlerts" in _code_only(_fun_body(kotlin, "onResume"))


def test_the_resume_branch_asks_what_is_on_screen_before_clearing() -> None:
    """La metà che impedisce il ritorno del difetto: in ``onResume`` la cancellazione
    dipende dalla risposta della SPA, non dal resume.

    Senza la domanda questo ramo *è* l'``onResume`` liscio di prima. Il confronto
    con ``"true"`` è la forma esatta che serve: ``evaluateJavascript`` restituisce
    ``"null"`` quando la SPA non c'è, e ``"null"`` è una stringa — un test di
    verità qualunque la prenderebbe per un sì.
    """
    body = _code_only(_fun_body(_main_activity(), "onResume"))
    assert "CHAT_ON_SCREEN_JS" in body
    clear_line = next(line for line in body.splitlines() if "clearAlerts" in line)
    assert '== "true"' in clear_line, clear_line


def test_the_chat_visible_question_reads_the_spa_view_that_exists() -> None:
    """``CHAT_ON_SCREEN_JS`` interroga ``mobileApp.currentMode``: se quel campo o il
    nome della vista cambiassero, la domanda risponderebbe sempre no — e gli
    alert resterebbero in coda per sempre, senza che niente fallisca."""
    kotlin = _main_activity()
    question = re.search(r"CHAT_ON_SCREEN_JS = \"\"\"(.*?)\"\"\"", kotlin, re.S)
    assert question, "CHAT_ON_SCREEN_JS non trovato"
    assert "currentMode === 'chat'" in question.group(1)
    app_js = _app_js()
    assert "this.currentMode = mode" in app_js
    assert "switchMode('chat'" in app_js


def test_entering_the_chat_view_notifies_the_native_shell() -> None:
    """L'unico modo in cui la chat arriva a schermo che il guscio nativo non può
    vedere da sé: un cambio vista dentro la WebView non produce callback
    d'activity. La chiamata sta in ``ChatController.activate``, che è il punto in
    cui la sezione diventa attiva, ed è difesa perché fuori dal guscio
    ``JennyNative`` non esiste."""
    chat_js = (UI_ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
    activate = re.search(r"\n  activate\(\)\s*\{(.*?)\n  \}", chat_js, re.S)
    assert activate, "activate() non trovato in mobile-chat.js"
    body = activate.group(1)
    assert "JennyNative?.chatOpened?.()" in body
    assert "try {" in body
    assert '@JavascriptInterface' in _main_activity().split("fun chatOpened")[0][-200:]


def test_a_cold_start_from_the_alert_still_lands_in_chat() -> None:
    """Con l'activity morta il tap non passa da ``onNewIntent``: l'intent arriva
    a ``onCreate``, dove non c'è ancora nessuna SPA da instradare. La richiesta
    deve quindi arrivare fino all'URL iniziale, come già fa il ritorno da un
    ripristino.
    """
    kotlin = _main_activity()
    assert "openChatOnLoad = true" in _code_only(_fun_body(kotlin, "onCreate"))
    assert "openChatOnLoad" in _code_only(_fun_body(kotlin, "buildGatewayUrl"))


# ── Quel che il guscio grida, qualcuno deve sentirlo ─────────────────────


def test_every_event_the_shell_dispatches_has_a_listener() -> None:
    """Il guscio parla alla WebUI anche per eventi, non solo per chiamate: la
    WebView vede cose che il JS dentro la pagina non puo' vedere, e gliele
    rigira.

    **Un evento senza ascoltatore non fallisce.** Non c'e' un errore, non c'e'
    una riga nel log: cade nel vuoto, e quel che doveva succedere semplicemente
    non succede. E' andata cosi' per `jenny-subframe-error`, che il guscio manda
    quando l'iframe di una mini-app non carica: l'ascolto stava nel costruttore
    della scheda «App», e quando quella schermata e' stata cancellata se n'e'
    andato con lei. Da allora una mini-app che non parte e' un riquadro bianco —
    compreso il caso piu' frequente, il cleartext bloccato dalla policy
    dell'APK, che senza quella scritta si vede solo in logcat.

    Il banco guarda dalla parte che non si puo' dimenticare: l'elenco lo detta
    il guscio, non noi. Un evento nuovo di la' arriva qui rosso finche' non ha
    un orecchio.
    """
    kotlin = "\n".join(
        p.read_text(encoding="utf-8") for p in JAVA.rglob("*.kt")
    )
    eventi = set(re.findall(r"new (?:Custom)?Event\('([\w-]+)'", kotlin))
    assert eventi, "nessun evento nel guscio: la ricerca non guarda piu' dove deve"

    ascolti = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in UI_ASSETS.rglob("*.js")
        if "vendor" not in p.relative_to(UI_ASSETS).parts
    )
    sordi = sorted(e for e in eventi if f"addEventListener('{e}'" not in ascolti)
    assert not sordi, (
        f"il guscio manda questi eventi e in pagina non li ascolta nessuno: {sordi}. "
        f"Cadono nel vuoto in silenzio — nessun errore, e la cosa che dovevano "
        f"far succedere semplicemente non succede."
    )
