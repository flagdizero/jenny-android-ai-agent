"""Il tasto Indietro di Android: una pressione, un cambiamento visibile.

Il guscio nativo inoltra *sempre* il back alla SPA (``OnBackPressedCallback(true)``
in ``MainActivity``), quindi qui non c'è niente che arrivi gratis: né la chiusura
dei ``<dialog>``, né la risalita dentro una sezione. Prima la SPA conosceva solo i
tab, e tutto il resto (dialog, drawer, cartelle del workspace, step
dell'onboarding) non esisteva per la history: il back scavalcava il livello in cui
l'utente si trovava. In più due sorgenti impilavano entry gemelle — il boot
(``replaceState`` + ``switchMode`` che pusha) e ``api.reload()`` (assegnare
``location.hash`` *è* una navigazione) — e su quelle la pressione veniva ingoiata
in silenzio: da lì la sensazione che il tasto "saltasse" le pagine.

Il contratto è: catena di consumatori dall'alto verso il basso, un unico punto di
scrittura della history, nessuna entry gemella.

Asserzioni sul sorgente, nello stile di ``test_thinking_scroll_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "mobile-app.js"
MAIN_ACTIVITY = ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "flagdizero" / "jenny" / "MainActivity.kt"


def _app() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


def _init_body(source: str) -> str:
    body = re.search(r"\n  async init\(\)\s*\{(.*?)\n  \}", source, re.S)
    assert body, "init() non trovato"
    return body.group(1)


def test_the_shell_hands_every_back_press_to_the_spa() -> None:
    """Il presupposto della catena: il nativo non ne gestisce nessun caso da sé.

    **Questo test asseriva ``OnBackPressedCallback(true)``**, cioè "il guscio
    intercetta sempre". Era il comportamento sbagliato: intercettare *sempre*
    include i momenti in cui la SPA non esiste — tutta la ripartenza del gateway
    (fino a ``BOOT_POLL_TIMEOUT_MS``) e, per sempre, la schermata d'errore, dove
    l'unico comando è il pulsante Riprova. Lì il callback prendeva la pressione e
    non ne faceva niente, senza lasciarla a nessun altro.

    Il contratto vero non è "intercetta sempre", è "quando c'è una SPA, la
    pressione va a lei e a nessun altro": il callback nasce disabilitato e vive
    esattamente quanto la pagina a schermo.
    """
    kotlin = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "OnBackPressedCallback(false)" in kotlin
    assert "OnBackPressedCallback(true)" not in kotlin
    assert "handleHardwareBack()" in kotlin


def test_the_layer_list_is_ordered_by_real_stacking() -> None:
    """L'ordine è quello di sovrapposizione: chi sta sopra si chiude per primo.

    Un livello fuori posto è invisibile nel codice ma non a schermo: chiudere
    (per dire) il drawer mentre una modale copre tutto lascia l'utente davanti
    alla stessa immagine, e la pressione sembra persa.

    **Questo test congelava l'ordine sbagliato.** Fino a ieri asseriva mini-app
    *prima* della minichat, cioè l'ordine che il codice aveva, non quello che gli
    z-index impongono: ``.app-frame-overlay`` 110 < ``.jenny-scrim`` 119 <
    ``.jenny-duo`` 120 < ``.jenny-mc`` 121 (e ``.app`` non crea stacking
    context). Con la minichat aperta sopra una mini-app, il back chiudeva l'app
    *sotto* e a schermo non cambiava niente. L'ordine asserito qui è ora quello
    misurato sul CSS.

    **Lightbox e minichat sono l'unica coppia fuori ordine, e non per sbaglio.**
    Con D3 (25/09/2026, «Jenny sempre sopra», anche alle immagini) la lightbox è
    scesa da 1000 a 115, sotto di lei: la minichat (121) le starebbe sopra. Le
    due però non stanno mai aperte insieme — sopra una lightbox lei non prende
    tocchi, e mentre la minichat è aperta il suo scrim (119) copre la chat e
    un'immagine non si apre — quindi fra loro l'ordine della catena non si vede.
    Il banco controlla quelle due condizioni invece di fingere che l'ordine lo
    decidano i numeri.
    """
    body = _method(_app(), "_overlayLayers")
    layers = [
        "dialog[open]",                  # top layer: showModal() sta sopra ogni z-index
        ".image-lightbox",               # z-index 115 (v. sotto: mai con la minichat)
        ".jenny-mc.open",                # minichat, z-index 121
        ".app-frame-overlay",            # mini-app, z-index 110
        "this.drawer.activeDrawer",      # drawer
    ]
    positions = []
    for marker in layers:
        assert marker in body, f"livello mancante nell'elenco: {marker}"
        positions.append(body.index(marker))
    assert positions == sorted(positions), (
        "i livelli non sono in order di sovrapposizione reale (vedi z-index in mobile-style.css)"
    )

    css = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    z = {
        name: int(re.search(rf"\n{re.escape(name)} \{{[^}}]*?z-index: (\d+);", css).group(1))
        for name in (".image-lightbox", ".jenny-scrim", ".jenny-mc")
    }
    frame = re.search(r"\.app-frame-overlay \{[^}]*?z-index: (\d+);", css)
    assert frame, "livello della mini-app non trovato"
    # La lightbox sta sopra la mini-app e sotto lo scrim della minichat.
    assert int(frame.group(1)) < z[".image-lightbox"] < z[".jenny-scrim"] < z[".jenny-mc"], z
    # Le due condizioni che tengono lightbox e minichat separate.
    assert ":root:has(.image-lightbox) .jenny-duo { pointer-events: none; }" in css, (
        "sopra una lightbox lei aprirebbe la minichat: due livelli insieme, fuori ordine"
    )
    scrim_aperto = re.search(r"\n\.jenny-scrim\.open \{([^}]*)\}", css)
    assert scrim_aperto and "pointer-events: auto" in scrim_aperto.group(1), (
        "con la minichat aperta un'immagine della chat si aprirebbe da sotto lo scrim"
    )


def test_every_layer_declares_a_test_and_a_dismissal() -> None:
    """Un livello senza ``present()`` non è interrogabile da ``hasOverlayAbove``;
    uno senza ``dismiss()`` non è chiudibile da Home. Servono entrambi."""
    body = _method(_app(), "_overlayLayers")
    # Contati sui `name:`, non fissati a un numero: pinnare "sono cinque"
    # faceva fallire il test sul livello legittimo aggiunto domani, e per il
    # motivo sbagliato. Quello che conta è che nessun livello sia sprovvisto.
    layers = body.count("name: '")
    assert layers >= 5, "livelli spariti dall'elenco"
    assert body.count("present: ()") == layers
    assert body.count("dismiss: ()") == layers


def test_the_back_chain_walks_the_single_layer_list() -> None:
    """Il back non riscrive i livelli a mano: li percorre."""
    body = _method(_app(), "handleHardwareBack")
    steps = [
        "this._overlayLayers()",                              # 1..5 overlay
        "this.controllers[this.currentMode]?.handleBack?.()",  # sotto-stato di sezione
        "window.history.back()",                              # schermata precedente
    ]
    positions = []
    for marker in steps:
        assert marker in body, f"passo mancante nella catena del back: {marker}"
        positions.append(body.index(marker))
    assert positions == sorted(positions)
    # Un livello presente ma che non consuma (la mini-app durante i 200 ms della
    # dissolvenza di chiusura) non deve ingoiare la pressione.
    assert "layer.dismiss() !== false" in body
    # Una copia locale di un test di presenza è la ricomparsa della quarta
    # sorgente di verità: i livelli si aggiungono solo in _overlayLayers().
    for marker in ("dialog[open]", ".image-lightbox", ".jenny-mc", ".app-frame-overlay"):
        assert marker not in body, f"il back riscrive un livello invece di percorrerlo: {marker}"


def test_home_dismisses_every_layer_not_a_hand_picked_subset() -> None:
    """Home smontava tre livelli su cinque: lightbox e minichat restavano a
    schermo sopra la vista home, e i ``<dialog>`` venivano chiusi con ``close()``
    diretto — scavalcando chi la chiusura la rifiuta (v. il test qui sotto)."""
    dismiss_all = _method(_app(), "_dismissAllOverlays")
    assert "this._overlayLayers()" in dismiss_all, "lo smontaggio percorre l'elenco, non un sottoinsieme"

    body = _method(_app(), "goHome")
    assert "this._dismissAllOverlays()" in body
    assert "dialog[open]" not in body, "Home non chiude i dialog per conto suo"
    assert "closeApp()" not in body, "anche la mini-app passa dall'elenco"
    # Gli overlay non sono tutto: il sotto-stato di sezione (l'editor del
    # workspace, la sottocartella aperta) sopravviveva al cambio vista e Home lo
    # ritrovava intatto al rientro.
    assert "collapseToRoot?.()" in body, "Home deve collassare anche il sotto-stato delle sezioni"


def test_closing_a_dialog_goes_through_a_cancelable_event() -> None:
    """Chiamare ``close()`` scavalcherebbe chi rifiuta la chiusura.

    ``showRestartDialog`` (backup-flow) fa ``preventDefault()`` sul ``cancel``
    apposta: dopo un restore il riavvio non è opzionale. Back, Home ed Esc devono
    avere la stessa semantica di Esc nativo, non un potere in più: passano tutti
    e tre da ``_dismissTopDialog``.
    """
    body = _method(_app(), "_dismissTopDialog")
    assert "new Event('cancel', { cancelable: true })" in body
    assert ".close()" in body, "chi non rifiuta va comunque chiuso"
    assert "if (top.dispatchEvent" in body, "close() solo se il cancel non è stato prevenuto"

    layers = _method(_app(), "_overlayLayers")
    assert "this._dismissTopDialog()" in layers

    backup = (ASSETS / "shared" / "backup-flow.js").read_text(encoding="utf-8")
    assert "addEventListener('cancel', (e) => e.preventDefault())" in backup, (
        "sparito il dialog non annullabile: il giro via evento `cancel` non ha più motivo di esistere"
    )


def test_escape_is_the_same_chain_as_the_hardware_back() -> None:
    """Sul Titan 2 la tastiera è sempre sotto le dita: Esc è la scorciatoia
    primaria. Copriva due livelli su cinque (drawer e dialog) ed era inerte su
    lightbox, minichat, mini-app, sotto-stato di sezione e history — cioè
    sembrava funzionare."""
    source = _app()
    shortcut = re.search(r"keyboard\.register\('escape', \(\) => \{(.*?)\}\);", source, re.S)
    assert shortcut, "shortcut Escape non trovata"
    assert "this.handleHardwareBack();" in shortcut.group(1)
    assert "dialog[open]" not in shortcut.group(1)


def test_the_lightbox_no_longer_keeps_its_own_escape_listener() -> None:
    """Instradato Esc sulla catena, il listener locale è la stessa divergenza che
    la catena elimina. Il resto del cleanup però deve restare: ``__jennyClose``
    è l'unico modo di chiudere la lightbox senza perdere ``onClose``
    (revokeObjectURL)."""
    lightbox = (ASSETS / "shared" / "image-lightbox.js").read_text(encoding="utf-8")
    assert "addEventListener('keydown'" not in lightbox
    assert "'Escape'" not in lightbox
    assert "overlay.__jennyClose = close;" in lightbox

    layers = _method(_app(), "_overlayLayers")
    assert "__jennyClose" in layers, (
        "la lightbox va chiusa dal proprio handler: remove() salta il cleanup"
    )


def test_the_layers_are_dismissed_through_public_entry_points() -> None:
    """``_setOut(false)`` e ``overlay.remove()`` sono scorciatoie che saltano il
    cleanup dei rispettivi proprietari (listener su ``document``, object URL,
    tastiera da abbassare). La shell parla solo con gli ingressi pubblici."""
    layers = _method(_app(), "_overlayLayers")
    assert "this.jenny?.handleBack()" in layers
    assert "this._appsActions?.handleBack()" in layers
    assert "this.drawer.closeAll()" in layers
    assert "_setOut(" not in _app(), "la shell non tocca lo stato interno della mascotte"


def test_the_two_dangerous_collaborators_of_the_chain_still_exist() -> None:
    """Gli anelli che il piano ha deciso di **non** ammutolire.

    ``_overlayLayers`` chiama ``this._appsActions?.handleBack()`` e
    ``this.jenny?.handleBack()``: l'optional chaining copre l'*oggetto* assente
    (controller non ancora istanziato, mascotte spenta), non il *metodo*. Se un
    domani uno dei due metodi sparisce o viene rinominato, la catena solleva un
    TypeError — che finisce in ``window.onerror`` → toast → ``/api/client-log``,
    quindi si vede.

    La tentazione era ammorbidire in ``?.handleBack?.()``. Sarebbe stato il fix
    sbagliato: trasformerebbe una rottura rumorosa in un Indietro che, con la
    minichat o una mini-app aperta, salta *due* livelli in silenzio — l'overlay
    resta a schermo e la pressione se ne va a chiudere la sezione sotto. Gli
    anelli si coprono, non si ammutoliscono: questo test è quella copertura.

    Il test dei collaboratori pinnava solo workspace e onboarding — i due
    chiamati con ``?.handleBack?.()``, cioè quelli che una sparizione la
    perdonano. I due pericolosi erano scoperti.
    """
    layers = _method(_app(), "_overlayLayers")
    assert "?.handleBack?.()" not in layers, (
        "l'optional call sul metodo trasformerebbe la rottura in un salto silenzioso di due livelli"
    )

    apps = (ASSETS / "shared" / "apps-actions.js").read_text(encoding="utf-8")
    apps_back = _method(apps, "handleBack")
    assert "if (!open) return false;" in apps_back, (
        "senza mini-app aperta il livello deve lasciar proseguire la catena"
    )
    assert "return true;" in apps_back
    assert "closeApp()" in apps_back
    # Il livello mini-app ha anche un `close` per Home: smonta e basta.
    assert "closeApp() {" in apps, "closeApp è il congedo che Home usa sul livello mini-app"

    jenny = (ASSETS / "mobile-jenny.js").read_text(encoding="utf-8")
    jenny_back = _method(jenny, "handleBack")
    assert "return false;" in jenny_back, (
        "a minichat chiusa il livello deve lasciar proseguire la catena"
    )
    assert "this._setOut(false)" in jenny_back, (
        "la minichat si chiude dal proprio ingresso: scrim, tastiera e stato dell'arte inclusi"
    )
    assert "return true;" in jenny_back


def test_the_lightbox_layer_and_its_owner_agree_on_class_and_handle() -> None:
    """Contratto cross-file, e nessuno dei due lati sa dell'altro.

    Il livello lightbox della catena cerca ``.image-lightbox`` nel documento e
    chiama ``overlay.__jennyClose``: due stringhe scritte in ``mobile-app.js``
    e onorate in ``shared/image-lightbox.js``. Rinominare la classe (o il
    campo) da una parte sola non rompe niente di visibile — semplicemente
    Indietro smette di vedere la lightbox e chiude la schermata sotto, con
    l'immagine ancora a tutto schermo. Qui i due lati vengono confrontati.
    """
    layers = _method(_app(), "_overlayLayers")
    lightbox = (ASSETS / "shared" / "image-lightbox.js").read_text(encoding="utf-8")

    assert ".image-lightbox" in layers
    assert "overlay.className = 'image-lightbox';" in lightbox, (
        "la classe che la catena interroga non è più quella che l'overlay si mette"
    )
    assert "__jennyClose" in layers
    assert "overlay.__jennyClose = close;" in lightbox, (
        "senza l'handle esposto la catena ripiega su remove(), che salta onClose "
        "(e con lui revokeObjectURL: gli object URL del workspace restano appesi)"
    )
    # Il ripiego deve restare, ma resta un ripiego.
    assert "lightbox.remove()" in layers


def test_the_drawer_layer_and_its_manager_agree_on_state_and_dismissal() -> None:
    """Stesso genere di contratto: la catena legge ``drawer.activeDrawer`` come
    test di presenza e chiama ``drawer.closeAll()`` come congedo. Sono due
    membri di ``DrawerManager`` scritti a mano in ``mobile-app.js``; se il
    manager smettesse di tenere ``activeDrawer`` aggiornato, il livello
    diventerebbe invisibile alla catena *e* a ``hasOverlayAbove`` — cioè il
    type-ahead della chat tornerebbe a rubare il fuoco sotto un drawer aperto.
    """
    layers = _method(_app(), "_overlayLayers")
    drawer = (ASSETS / "mobile-drawer.js").read_text(encoding="utf-8")

    assert "this.drawer.activeDrawer" in layers
    assert "this.activeDrawer = null;" in drawer, "il manager deve azzerare lo stato alla chiusura"
    assert re.search(r"this\.activeDrawer = id;", drawer), (
        "il manager deve marcare quale drawer è aperto: è il test di presenza della catena"
    )
    assert "this.drawer.closeAll()" in layers
    assert "\n  closeAll() {" in drawer, "closeAll è l'ingresso pubblico che la catena usa"


def test_the_type_ahead_guard_consumes_the_layer_list() -> None:
    """Il quarto posto in cui erano scritti i livelli. Guardando solo
    ``dialog[open]``, con la lightbox (o la minichat, o una mini-app) aperta la
    chat rimetteva il fuoco sul composer coperto: i caratteri della tastiera
    fisica finivano in un campo invisibile."""
    chat = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
    guard = re.sub(r"//.*", "", _method(chat, "_maybeTypeAheadFocus"))  # i commenti citano il prima
    assert "window.mobileApp?.hasOverlayAbove()" in guard
    assert "dialog[open]" not in guard, "la guardia non riscrive i livelli per conto suo"

    api_body = _method(_app(), "hasOverlayAbove")
    assert "this._overlayLayers()" in api_body


def test_back_at_the_root_does_nothing_and_never_trusts_history_length() -> None:
    """Jenny è il launcher: sotto la radice non c'è nessuna app a cui tornare.

    ``history.length`` non sa rispondere: conta l'intera sessione del WebView
    (iframe delle mini-app, reload) e non cala mai.
    """
    body = _method(_app(), "handleHardwareBack")
    assert "this._navPos > 0" in body, "il fondo dello stack va riconosciuto dalla posizione nostra"
    assert "history.length" not in body


"""Esenzioni dell'imbuto della history, per *path* e non per basename.

``jenny-sdk.js`` gira dentro l'iframe della mini-app (history sua, v.
``test_mini_app_navigation_contract``) e ``shared/api-client.js`` riscrive la
entry corrente nel reload (v. il test dedicato qui sotto). Prima erano elencati
per nome del file: un futuro ``assets/qualcosa/api-client.js`` sarebbe stato
esentato in silenzio, cioè avrebbe potuto scrivere la history senza che nessuno
se ne accorgesse. Anche ``vendor/`` va escluso per path: la libreria di terzi
non è codice nostro, ma "vendor" come nome di file non deve esentare niente.
"""
FUNNEL_EXEMPT = {
    # Unico esente rimasto: riscrive il *fragment* dell'URL corrente (segreto di
    # bootstrap), non impila né sostituisce schermate. L'SDK delle mini-app era
    # esente anche lui finché scriveva la history dall'iframe; ora non la tocca
    # più (v. test_mini_app_navigation_contract), quindi l'esenzione è caduta:
    # lasciarla in piedi avrebbe tenuto aperta la strada per rimettercela.
    Path("shared") / "api-client.js",
}


def test_every_history_write_goes_through_the_single_funnel() -> None:
    """Un solo punto di scrittura, altrimenti le entry tornano ad accumularsi."""
    seen_exempt: set[Path] = set()
    for js in sorted(ASSETS.rglob("*.js")):
        rel = js.relative_to(ASSETS)
        if "vendor" in rel.parts or js == APP_JS:
            continue
        if rel in FUNNEL_EXEMPT:
            seen_exempt.add(rel)
            continue
        source = js.read_text(encoding="utf-8")
        assert "history.pushState" not in source, f"{rel} scrive la history fuori da pushNav"
        assert "history.replaceState" not in source, f"{rel} scrive la history fuori da replaceNav"
    assert seen_exempt == FUNNEL_EXEMPT, (
        "un'esenzione dell'imbuto punta a un file che non esiste più: "
        f"{sorted(str(p) for p in FUNNEL_EXEMPT - seen_exempt)}"
    )


def test_the_funnel_stamps_the_stack_position_and_refuses_twins() -> None:
    source = _app()
    push = _method(source, "pushNav")
    assert "pos: this._navPos" in push, "senza posizione il back non sa dov'è il fondo"
    assert "this.replaceNav(state)" in push, (
        "impilare due volte la stessa schermata regala una pressione di Indietro che non cambia niente"
    )
    assert "pos: this._navPos" in _method(source, "replaceNav")


def test_the_boot_does_not_stack_a_twin_of_the_initial_view() -> None:
    """La entry iniziale *è* la vista iniziale: si riscrive, non si impila.

    **Aggiornato.** Il test citava a memoria due righe intere, nomi delle
    variabili locali compresi: rinominare ``initialWiki`` — un'operazione che
    non cambia niente per nessuno — lo faceva fallire, e un rosso che non
    corrisponde a un difetto insegna solo a non fidarsi del rosso. Il contratto
    non è come si chiamano le variabili: è che nel boot la radice venga
    *riscritta* prima dello switch, e che lo switch non impili. Si verifica per
    posizione e per forma della chiamata.
    """
    body = _init_body(_app())
    marks = [m.start() for m in re.finditer(r"this\.replaceNav\(this\._navStateFor\(", body)]
    assert marks, "il boot non riscrive più la entry iniziale"
    switches = [m for m in re.finditer(r"this\.switchMode\((\w+), (\w+)\)", body)]
    assert switches, "il boot non entra più in nessuna vista"
    boot_switch = switches[-1]
    assert boot_switch.group(2) == "false", (
        "switchMode con push impilerebbe una gemella della radice appena riscritta"
    )
    assert any(mark < boot_switch.start() for mark in marks), (
        "la radice va riscritta *prima* dello switch, altrimenti restano due entry identiche "
        "e il primo Indietro viene ingoiato senza cambiare niente a schermo"
    )
    assert "this.pushNav(" not in body, "il boot non impila: riscrive"


def test_the_reload_leaves_no_ghost_entry() -> None:
    """``location.hash = ...`` è una navigazione: un fantasma per ogni reload."""
    source = (ASSETS / "shared" / "api-client.js").read_text(encoding="utf-8")
    reload_body = re.search(r"\n  reload\(\)\s*\{(.*?)\n  \}", source, re.S)
    assert reload_body, "reload() non trovato"
    code = re.sub(r"//.*", "", reload_body.group(1))  # il commento cita ciò che non si fa
    assert "location.hash =" not in code
    assert "history.replaceState" in code


def test_sections_with_their_own_depth_expose_a_back_handler() -> None:
    """Cartelle ed editor del workspace, step dell'onboarding: risalire dentro
    la sezione viene prima di uscirne.

    **Aggiornato.** Questo test pinnava lo smontaggio dell'editor *scritto
    dentro* ``handleBack`` (``return !ret;`` sulla riga dopo
    ``backToExplorerAt``), cioè la forma che rendeva possibile il difetto: se il
    teardown vive nel ramo del back, ogni altro percorso di uscita se ne scrive
    uno suo — ed è esattamente ciò che era successo, con due smontaggi e zero
    guard sul buffer sporco. La regola sul non impilare una entry in avanti non
    è cambiata: si è spostata nel teardown unico, ed è lì che va verificata
    (v. ``test_unsaved_work_contract.py``).
    """
    workspace = (ASSETS / "mobile-workspace.js").read_text(encoding="utf-8")
    # Il file aperto è la vista `workspace`, e il suo back la smonta.
    ws_back = _method(workspace, "handleBack")
    assert "this._closeEditor({ hardwareBack: true })" in ws_back, (
        "il back non smonta l'editor per conto suo: passa dal teardown unico"
    )
    close_editor = _method(workspace, "_closeEditor")
    assert "if (hardwareBack) return false;" in close_editor, (
        "sotto c'è già la entry di Memoria: il back prosegue e ci arriva da sé"
    )

    # La cartella, invece, è un livello **dentro Memoria** dal 21/09/2026:
    # l'esploratore ha lasciato la sua vista ed è una scheda del cassetto. La
    # regola non è cambiata — risalire viene prima di uscire — ma la pressione
    # arriva al controller del cassetto, che la gira qui.
    card_back = _method(workspace, "handleCardBack")
    assert "parentPath(this.currentDir)" in card_back, "il back deve risalire di una cartella"
    assert "return true;" in card_back, "risalire è un cambiamento visibile: consuma la pressione"
    settings_back = _method((ASSETS / "mobile-settings.js").read_text(encoding="utf-8"), "handleBack")
    assert "handleCardBack" in settings_back, (
        "il cassetto non gira la pressione al gestore file: da tre cartelle di "
        "profondità una sola pressione porterebbe fuori dall'intera schermata"
    )

    onboarding = (ASSETS / "mobile-onboarding.js").read_text(encoding="utf-8")
    onb_back = _method(onboarding, "handleBack")
    assert "_goToStep0()" in onb_back and "_goBackToStep1()" in onb_back
    assert "return true;" in onb_back, "dall'onboarding non si esce col back"


def test_the_editor_has_one_origin_and_does_not_remember_it_as_state() -> None:
    """``_returnMode`` era una promessa sullo *stack*, non una preferenza.

    Valeva "sotto la entry dell'editor c'è quella della sezione da cui l'ho
    aperto". Uscendo dal Workspace con l'editor aperto quella promessa scadeva
    — l'utente era andato altrove — ma il flag restava valorizzato, e al
    rientro una sola pressione di Indietro chiudeva l'editor *e* portava fuori
    dalla sezione. Due cambiamenti visibili per una pressione. La toppa era
    azzerarlo in ``deactivate()``.

    Dal 21/09/2026 la domanda non si pone: l'esploratore è la scheda «I file
    veri» di Memoria, e **nient'altro apre un file**. Un campo con un valore
    solo non descrive niente, e si porta dietro l'azzeramento che è la vera
    superficie del difetto. Qui si misura che non torni: non il nome, ma la
    forma — una destinazione letta da uno stato invece che scritta dove serve.
    """
    workspace = (ASSETS / "mobile-workspace.js").read_text(encoding="utf-8")
    codice = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", workspace, flags=re.S))
    assert "_returnMode" not in codice, "la destinazione è tornata a essere uno stato"
    # E l'origine unica va *scritta*, o `_closeEditor` non sa dove riportare.
    assert "navigateBack('memory')" in codice, "l'uscita dal file non nomina la sua origine"
    apri = _method(workspace, "_enterEditorView")
    assert "switchMode('workspace')" in apri, (
        "aprire un file deve impilare la propria schermata: senza, il file si "
        "disegna sotto la scheda e Indietro non ha niente da togliere"
    )


def test_the_session_info_popover_is_a_layer_of_its_own() -> None:
    """Il popover "Info sessione" copre la chat e ha una sua chiusura, ma il back
    lo scavalcava: usciva dalla chat lasciandolo a schermo."""
    chat = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
    back = _method(chat, "handleBack")
    assert "this._sessionInfoPopover" in back
    assert "this._hideSessionInfo()" in back
    assert "return false;" in back, "senza popover il back deve proseguire la catena"


def test_leaving_the_chat_takes_the_popover_with_it() -> None:
    """Il popover è appeso a ``document.body``, non alla view: il
    ``display: none`` del cambio sezione non lo tocca. Restava sopra la sezione
    nuova, col proprio ``setInterval`` da 1s ancora vivo."""
    chat = (ASSETS / "mobile-chat.js").read_text(encoding="utf-8")
    assert "document.body.appendChild(popover)" in chat, (
        "se il popover tornasse dentro la view, questo teardown non servirebbe più"
    )
    assert "this._hideSessionInfo();" in _method(chat, "deactivate")


def test_the_drawers_only_sub_screen_is_the_folder_you_are_in() -> None:
    """Il catalogo modelli era un livello dentro le impostazioni: ci si
    arrivava da un pulsante, lo si scorreva, e senza `handleBack` una
    pressione ne saltava due di schermate. Dal 20/09/2026 quel catalogo e' in
    casa, dove e' una **stanza** e non un sotto-livello.

    Per un giorno qui non e' rimasto niente da sbucciare. Poi il gestore file
    e' entrato nella scheda «I file veri» (21/09/2026) e ha portato con se'
    l'unico sotto-livello che i cassetti abbiano: la cartella in cui si sta.

    Quel che il banco misura non e' che ci sia un `true`, ma che il `true`
    arrivi **solo** da li'. Un `handleBack` che consuma per conto suo si
    mangerebbe la pressione senza chiudere niente, ed e' il difetto che il
    catalogo modelli aveva reso concreto.
    """
    settings = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")
    back = _method(settings, "handleBack")
    codice = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", back, flags=re.S)).strip()
    ritorni = re.findall(r"return\b[^;]*;", codice)
    assert sum("handleCardBack" in r for r in ritorni) == 1
    assert all(r == "return false;" or "handleCardBack" in r for r in ritorni), (
        "il cassetto decide da se' invece di girare la domanda: l'unico altro "
        "ritorno ammesso e' il `false` dei cassetti senza gestore file"
    )
    assert "?? false" in codice, (
        "senza gestore file agganciato la pressione deve proseguire la catena"
    )
    assert "handleCardBack" in codice, "l'unico sotto-livello e' la cartella del gestore file"
    assert "return true" not in back, (
        "handleBack si tiene una pressione per un livello che non esiste piu'"
    )
