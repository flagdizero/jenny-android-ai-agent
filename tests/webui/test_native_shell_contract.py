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

import pytest
from support.kotlin_source import read_source

ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android" / "app" / "src" / "main"
JAVA = ANDROID / "java" / "com" / "flagdizero" / "jenny"
MANIFEST = ANDROID / "AndroidManifest.xml"
MAIN_ACTIVITY = JAVA / "MainActivity.kt"
NOTIFIER = JAVA / "NotifierBridge.kt"
LAYOUT = ANDROID / "res" / "layout" / "activity_main.xml"
UI_ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"


def _main_activity() -> str:
    return read_source(MAIN_ACTIVITY)


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


def _until_blank_line(source: str, name: str) -> str:
    """Una funzione a espressione (``fun f(): T = ...``), fino alla riga vuota."""
    match = re.search(rf"\bfun {re.escape(name)}\s*\(", source)
    assert match, f"funzione {name} non trovata"
    end = source.find("\n\n", match.end())
    return source[match.start() : end if end >= 0 else len(source)]


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
    chat, la mini-app aperta e la connessione WebSocket. ``onSaveInstanceState``
    salva solo il path di un export in corso: la SPA non la rimette a posto
    niente.

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
        # Sett 2026: una tastiera Bluetooth che si attacca cambia `keyboard` (e
        # spesso `navigation`), che `keyboardHidden` non copre; `layoutDirection`
        # accompagna `locale` quando la lingua è RTL.
        "keyboard",
        "navigation",
        "layoutDirection",
        # 26/09/2026: il «testo in grassetto» di Accessibilità (API 31),
        # accanto alla dimensione carattere; e due cambi senza risorse da
        # ri-risolvere (v. il test qui sotto).
        "fontWeightAdjustment",
        "colorMode",
        "touchscreen",
    ):
        assert required in tokens, f"configChanges senza {required}"


def test_absorbing_color_mode_and_touchscreen_is_safe_because_no_resource_follows_them() -> None:
    """``colorMode`` e ``touchscreen`` si assorbono perché nessuna cartella di
    risorse usa i loro qualificatori: se un giorno ne comparisse una, l'app la
    ignorerebbe fino al riavvio. ``mcc``/``mnc`` restano fuori apposta."""
    res = ANDROID / "res"
    qualified = [
        d.name
        for d in res.iterdir()
        if d.is_dir()
        and any(q in d.name.split("-") for q in ("widecg", "nowidecg", "highdr", "lowdr",
                                                   "notouch", "finger", "stylus"))
    ]
    assert qualified == [], qualified
    tokens = _activity_config_changes()
    assert "mcc" not in tokens and "mnc" not in tokens


def test_absorbing_uimode_is_safe_because_nothing_native_follows_it() -> None:
    """Assorbire ``uiMode`` senza ricreare è sicuro solo se nessuna risorsa
    dipende dalla modalità notte. Se un domani ``Theme.Jenny`` diventasse
    ``DayNight``, o il CSS della WebUI iniziasse a usare ``prefers-color-scheme``,
    l'app resterebbe coi colori vecchi fino al riavvio — e nessuno collegherebbe
    la cosa a questa riga di manifest.
    """
    # Anche values-v31/, dove stanno gli splash per tema.
    for themes in sorted((ANDROID / "res").glob("values*/themes.xml")):
        assert "DayNight" not in themes.read_text(encoding="utf-8"), themes
    non_vendor_css = [
        path
        for path in UI_ASSETS.rglob("*.css")
        if "vendor" not in path.parts and "prefers-color-scheme" in path.read_text(encoding="utf-8")
    ]
    assert non_vendor_css == [], f"prefers-color-scheme in {non_vendor_css}"


def test_absorbing_locale_is_safe_because_the_layout_has_no_string_resources() -> None:
    """Stessa condizione per ``locale``: il layout nativo scrive le sue sei
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
    notifier = read_source(NOTIFIER)
    assert "MainActivity.openChatIntent(context)" in notifier
    kotlin = _main_activity()
    assert re.search(r"\bconst val ACTION_OPEN_CHAT\b", kotlin)
    builder = _code_only(_until_blank_line(kotlin, "openChatIntent"))
    assert ".setAction(ACTION_OPEN_CHAT)" in builder


def test_only_our_open_chat_clears_the_alerts() -> None:
    """L'activity è esportata (è il launcher): l'action la scrive chiunque, e il
    ramo del tap **cancella gli avvisi**. Un'altra app poteva far sparire dalla
    tendina i messaggi proattivi non letti.
    Ora l'intent nostro porta un gettone casuale tenuto nelle preferenze private,
    e i due rami — ``onNewIntent`` e il gemello in ``onCreate`` — lo esigono."""
    kotlin = _main_activity()
    builder = _code_only(_until_blank_line(kotlin, "openChatIntent"))
    assert ".putExtra(EXTRA_OPEN_CHAT_TOKEN, openChatToken(context))" in builder
    check = _code_only(_fun_body(kotlin, "isOurOpenChat"))
    assert "getStringExtra(EXTRA_OPEN_CHAT_TOKEN)" in check
    assert "MessageDigest.isEqual(" in check
    token = _code_only(_fun_body(kotlin, "openChatToken"))
    assert "SecureRandom()" in token and "Context.MODE_PRIVATE" in token
    for fun in ("onNewIntent", "onCreate"):
        body = _code_only(_fun_body(kotlin, fun))
        assert "isOurOpenChat(this, intent)" in body, f"{fun} non controlla il gettone"
        assert "intent?.action == ACTION_OPEN_CHAT" not in body, (
            f"{fun}: l'action da sola non basta più"
        )
    # Chi porta in chat dall'interno usa lo stesso costruttore, gettone compreso.
    for sender in (NOTIFIER, JAVA / "FloatingOverlayController.kt"):
        src = _code_only(read_source(sender))
        assert "setAction(MainActivity.ACTION_OPEN_CHAT)" not in src
        assert "MainActivity.openChatIntent(" in src


def test_alerts_left_from_before_an_update_get_the_token_too() -> None:
    """Un alert postato da una versione senza gettone, ancora in tendina dopo
    l'aggiornamento, al tocco non portava in chat. Dopo ``MY_PACKAGE_REPLACED``
    il ricevitore richiede il ``PendingIntent`` di ogni alert in tendina con lo
    stesso codice, la stessa action e gli stessi flag: ``FLAG_UPDATE_CURRENT``
    ne riscrive gli extra al suo posto, gettone compreso."""
    notifier = read_source(NOTIFIER)
    tap = _code_only(_until_blank_line(notifier, "alertTapIntent"))
    assert "tag.hashCode()" in tap, "il codice di sempre: e' quello che identifica l'intent"
    assert "MainActivity.openChatIntent(context)" in tap
    assert "PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE" in tap
    builder = _code_only(_fun_body(notifier, "baseBuilder"))
    assert ".setContentIntent(alertTapIntent(context, tag))" in builder, (
        "un alert nuovo e un alert rinfrescato devono chiedere lo stesso intent"
    )
    refresh = _code_only(_fun_body(notifier, "refreshAlertTapIntents"))
    assert "activeNotifications" in refresh
    assert "channelId == CHANNEL_ID" in refresh and "tag != FAILED_TAG" in refresh
    assert "alertTapIntent(context, it)" in refresh
    assert "catch (e: Exception)" in refresh, "non deve far cadere il ricevitore"
    boot = _code_only(_fun_body(read_source(JAVA / "BootReceiver.kt"), "onReceive"))
    guarded = boot[boot.index("action == Intent.ACTION_MY_PACKAGE_REPLACED") :]
    assert "NotifierBridge.refreshAlertTapIntents(" in guarded
    assert boot.index("refreshAlertTapIntents(") < boot.index("GatewayStarter.ensureUp(")


def test_tapping_the_alert_brings_the_chat_without_taking_the_place() -> None:
    """Il ramo deve portare a schermo la chat — il messaggio che l'utente ha
    toccato sta lì — e nella casa **senza togliere il posto**: la chat sale
    sopra la pagina in cui eri, mini-app o quaderno che sia (dal 10/10/2026;
    prima smontava i livelli e tornava alla pagina chat).

    Il guscio non compone più il comportamento da sé. ``goHome()`` seguito da
    ``switchMode('chat', false)`` lasciava la entry di radice a descrivere la
    *vista home* mentre a schermo c'era la chat: con una vista home diversa da
    chat, il primo Indietro atterrava dove l'utente non era mai stato. La SPA
    espone ``openChat()``, che è il comportamento intero in un punto solo.
    """
    kotlin = _main_activity()
    on_new_intent = _code_only(_fun_body(kotlin, "onNewIntent"))
    assert "isOurOpenChat(this, intent)" in on_new_intent
    assert "OPEN_CHAT_JS" in on_new_intent
    open_chat_js = re.search(r"OPEN_CHAT_JS = \"\"\"(.*?)\"\"\"", kotlin, re.S)
    assert open_chat_js, "OPEN_CHAT_JS non trovato"
    body = open_chat_js.group(1)
    assert "app.openChat()" in body
    assert "goHome()" not in body, "il guscio non ricompone il comportamento a mano"

    # E il lato SPA deve davvero farlo, non solo esistere. Dal 29/09/2026
    # l'officina non apre piu' la sua Console — l'ultima vista usata, magari un
    # quaderno — ma va a casa, che nasce sulla chat personale (collaudo del
    # 27/09). Torna false: la chat li' non si e' aperta, e gli avvisi li
    # cancella la casa quando la mostra.
    open_chat = _method(_app_js(), "openChat")
    assert "api.navigate('/html-mobile/', { replace: true })" in open_chat
    assert "return false;" in open_chat
    home = _method((UI_ASSETS / "home-app.js").read_text(encoding="utf-8"), "openChat")
    assert "this.openChatOver({ key: sessionManager.personalKey })" in home, (
        "la casa deve aprire la conversazione personale sopra la pagina"
    )
    assert "_closeAllOverlays" not in home, "l'avviso chiude di nuovo l'app aperta"
    assert "_setView(" not in home, "l'avviso cambia di nuovo stanza"


def test_home_from_another_app_brings_the_house_back_where_it_was() -> None:
    """Jenny è il launcher: ogni Home, anche quello che ti riporta qui da
    un'altra app, arriva come intent ``CATEGORY_HOME`` a ``onNewIntent``.
    Solo quello premuto con la casa già davanti la riporta alla chat
    (``goHome``); quello del rientro no — altrimenti ogni giro in un'altra app
    finiva in chat, fuori dal quaderno e dalla mini-app (10/10/2026).

    «Eri via» lo dice ``onStop``, e il segno si spegne a fine ``onResume``,
    che per un'activity riportata davanti arriva **dopo** ``onNewIntent``.
    """
    kotlin = _main_activity()
    on_new_intent = _code_only(_fun_body(kotlin, "onNewIntent"))
    home_branch = re.search(r"if \(intent\?\.hasCategory\(Intent\.CATEGORY_HOME\) == true(.*?)\)\s*\{(.*?)\}",
                            on_new_intent, re.S)
    assert home_branch, "il ramo Home non c'è più"
    assert "!cameBackFromBackground" in home_branch.group(1), "Home dal rientro riporta di nuovo alla chat"
    assert "goHome()" in home_branch.group(2)
    assert "cameBackFromBackground = true" in _code_only(_fun_body(kotlin, "onStop"))
    on_resume = _code_only(_fun_body(kotlin, "onResume")).strip()
    assert on_resume.rstrip().endswith("cameBackFromBackground = false"), (
        "il segno va spento in fondo a onResume, dopo che onNewIntent l'ha letto"
    )
    assert "cameBackFromBackground = false" not in _code_only(_fun_body(kotlin, "onPause"))


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


def test_the_chat_visible_question_asks_a_method_every_shell_has() -> None:
    """``CHAT_ON_SCREEN_JS`` chiede ``mobileApp.isChatOnScreen()``.

    Leggeva ``mobileApp.currentMode``, un campo dell'officina: nella casa — il
    guscio di default — non c'era, la domanda rispondeva sempre no, e gli alert
    restavano in coda per sempre senza che niente fallisse. Questo stesso test
    guardava solo ``mobile-app.js``, cioe' il guscio in cui la domanda aveva
    senso, ed e' rimasto verde tutto il tempo (trovato il 24/09/2026)."""
    kotlin = _main_activity()
    question = re.search(r"CHAT_ON_SCREEN_JS = \"\"\"(.*?)\"\"\"", kotlin, re.S)
    assert question, "CHAT_ON_SCREEN_JS non trovato"
    assert "app.isChatOnScreen()" in question.group(1)
    assert "typeof app.isChatOnScreen === 'function'" in question.group(1)
    assert "currentMode" not in question.group(1)


@pytest.mark.parametrize("shell", ["mobile-app.js", "home-app.js"])
def test_both_shells_answer_the_chat_visible_question(shell: str) -> None:
    """Il guscio nativo non sa quale delle due interfacce ha caricato: il
    contratto vale per entrambe, o per una delle due non vale."""
    source = (UI_ASSETS / shell).read_text(encoding="utf-8")
    assert re.search(r"\n  isChatOnScreen\(\)\s*\{", source), f"{shell}: isChatOnScreen manca"


def test_the_workshop_answer_is_its_chat_mode() -> None:
    """La Console, e con la conversazione personale: se e' un quaderno, l'avviso
    li' non c'e' (collaudo del 27/09/2026)."""
    app_js = _app_js()
    answer = _method(app_js, "isChatOnScreen")
    assert "this.chatVisible()" in answer
    assert "sessionManager.currentKey === sessionManager.personalKey" in answer
    # La Console, o la chat aperta sopra una vista (05/10/2026): la chat c'e'.
    visible = _method(app_js, "chatVisible")
    assert "this.currentMode === 'chat'" in visible and "this._chatOver === true" in visible
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
    # Sta sulla porta dei comandi, che solo il frame principale della SPA
    # raggiunge (v. tests/security/test_native_bridge_origin.py): dall'iframe
    # di una Jenny App non si cancellano gli avvisi dell'utente.
    assert '"chatOpened" -> chatOpened()' in _main_activity()
    assert "'chatOpened'" in (UI_ASSETS / "shared" / "native-bridge.js").read_text("utf-8")


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
    kotlin = "\n".join(read_source(p) for p in JAVA.rglob("*.kt"))
    events = set(re.findall(r"new (?:Custom)?Event\('([\w-]+)'", kotlin))
    assert events, "nessun evento nel guscio: la ricerca non guarda piu' dove deve"

    listeners = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in UI_ASSETS.rglob("*.js")
        if "vendor" not in p.relative_to(UI_ASSETS).parts
    )
    deaf = sorted(e for e in events if f"addEventListener('{e}'" not in listeners)
    assert not deaf, (
        f"il guscio manda questi eventi e in pagina non li ascolta nessuno: {deaf}. "
        f"Cadono nel vuoto in silenzio — nessun errore, e la cosa che dovevano "
        f"far succedere semplicemente non succede."
    )
