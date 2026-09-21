"""Le invarianti della riga del cassetto, lette sul sorgente.

Compagno di ``test_launcher_rank_client.py``, che prova il motore sotto node.
Qui si guardano le tre proprietà del *foglio* che il passo 3 esiste per
ottenere, e che si perderebbero senza rumore:

* **difetto 02** — la ``description`` che il gateway manda per ogni skill e
  ogni Jenny App arriva davvero alla riga, invece di essere buttata per un
  nome troncato;
* **difetto 05** — un guasto compare *nella* riga e su una riga sola, non in un
  blocco che alza la cella (nella griglia di oggi l'errore porta la riga da 100
  a 147 px);
* **difetto 07** — digitare **non** ricostruisce l'elenco. È il difetto più
  facile da reintrodurre: basta chiamare ``_render()`` invece di
  ``_renderList()`` nell'ascoltatore del campo, e non si nota finché non si
  prova su un telefono con 68 voci e 47 icone base64.

Col passo 6 il file ha preso anche i *bordi*: che il foglio esista davvero nella
pagina (nessun test lo diceva, e il controller esce in silenzio se i nodi non ci
sono), la riga «Gestisci», i quattro stati vuoti distinti, l'avviso di elenco
incompleto e il toast di un avvio fallito.

Asserzioni sul sorgente, nello stile del resto di ``tests/webui/``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"


def _src(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


# ── difetto 02 — la descrizione arriva alla riga ────────────────────────────

def test_the_gateway_description_reaches_the_entry() -> None:
    body = _method(_src("shared/apps-source.js"), "launcherEntries")
    assert "app.description" in body, "la description delle Jenny App si perde"
    assert "app.description || ''" in body, "le Jenny App portano la loro description"
    assert "description: app.packageName" in body, (
        "le app Android non hanno description: al suo posto il pacchetto, che è "
        "un dato vero e si cerca"
    )
    assert "app.packageName" in body, (
        "le app Android non hanno descrizione: al suo posto va il pacchetto, "
        "che è un dato vero — non un testo inventato"
    )


def test_the_row_prints_the_secondary_line() -> None:
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "launcher-row-desc" in body
    assert "entry.problem || entry.description" in body, (
        "un guasto ha la precedenza sulla descrizione: è quello che spiega cosa fare"
    )


def test_the_secondary_line_is_searchable() -> None:
    """3.1: si cerca su nome **e** descrizione. Se `searchText` non arrivasse
    alla voce, la ricerca tornerebbe a essere solo sui nomi senza fallire."""
    body = _method(_src("shared/apps-source.js"), "launcherEntries")
    assert body.count("searchText:") == 2, (
        "entrambe le categorie del cassetto devono essere cercabili "
        "(le skill non ci sono più: v. test_no_skills_in_the_drawer)"
    )


# ── difetto 05 — il guasto non deforma la riga ──────────────────────────────

def test_the_error_line_stays_on_one_line() -> None:
    css = _src("mobile-style.css")
    desc = re.search(r"\.launcher-row-desc \{(.*?)\}", css, re.S)
    assert desc, ".launcher-row-desc non trovata"
    assert "white-space: nowrap" in desc.group(1), (
        "senza nowrap un errore lungo va a capo e alza la riga: è il difetto 05"
    )
    assert "text-overflow: ellipsis" in desc.group(1)
    assert ".launcher-row-desc--problem" in css, "il guasto non ha un colore proprio"


def test_the_error_is_rendered_as_text_not_html() -> None:
    """I nomi e gli errori arrivano da manifest scritti da un LLM e dal
    PackageManager: la riga si costruisce nel DOM, mai per concatenazione."""
    source = _src("mobile-launcher.js")
    assert "innerHTML" not in source, "il foglio non deve mai scrivere HTML grezzo"
    body = _method(source, "_buildRow")
    assert body.count("textContent") >= 3


def test_the_icon_src_accepts_only_data_images() -> None:
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "entry.icon.startsWith('data:image/')" in body, (
        "`src` accetta anche schemi che eseguono, e questo valore viene da fuori"
    )


# ── difetto 07 — digitare non ricostruisce l'elenco ─────────────────────────

def test_typing_reorders_and_never_rebuilds() -> None:
    source = _src("mobile-launcher.js")
    typing = _method(source, "_onQueryChanged")
    assert "_renderList()" in typing
    assert "_render()" not in typing, (
        "il percorso del tasto non deve passare da _render(): rileggerebbe le "
        "liste e ricostruirebbe le righe a ogni carattere (difetto 07)"
    )
    assert "_syncEntries" not in typing


def test_rows_are_cached_by_key_and_reused() -> None:
    source = _src("mobile-launcher.js")
    sync = _method(source, "_syncEntries")
    assert "cached.sig === sig" in sync, (
        "senza il confronto della firma ogni apps_list_changed butterebbe via "
        "le icone base64 già decodificate per riscriverle identiche"
    )
    render = _method(source, "_renderList")
    assert "this._rows.get(entry.key).el" in render, (
        "la lista deve rimettere in fila i nodi esistenti, non crearne di nuovi"
    )


def test_the_list_is_written_once_per_keystroke() -> None:
    render = _method(_src("mobile-launcher.js"), "_renderList")
    assert render.count("this.list.replaceChildren") == 3, (
        "una sola scrittura per ramo (caricamento, nessun risultato, risultati)"
    )
    assert "appendChild" not in render, "appendere una riga per volta è N reflow"


# ── il tocco ────────────────────────────────────────────────────────────────

def test_activation_is_delegated_to_one_listener_on_the_list() -> None:
    """Le righe si rimettono in fila a ogni tasto: un listener per riga li
    moltiplicherebbe per il numero di ricostruzioni."""
    source = _src("mobile-launcher.js")
    row = _method(source, "_buildRow")
    assert "addEventListener" not in row
    assert "this.list?.addEventListener('click'" in source


def test_the_launch_policy_lives_in_one_place() -> None:
    """«Aprire» vuol dire due cose diverse nei due spazi di nomi, e la scelta
    sta in **un** posto.

    Erano tre finche' c'erano le skill: quelle pero' non si lanciano — toccarne
    una apriva una scheda — e nel cassetto non sono mai entrate. Con la scheda
    «App» cancellata (21/09/2026) restano le due che si aprono davvero.

    Il foglio non decide e non parla con la rete: chiede alle azioni.
    """
    apps = _src("shared/apps-actions.js")
    body = _method(apps, "activateEntry")
    assert "this.launchAndroidApp(entry.id)" in body
    assert "this.openApp(entry.id)" in body
    assert "_openSkill" not in body, "le skill non si lanciano: nessun terzo ramo"
    launcher = _method(_src("mobile-launcher.js"), "_activate")
    assert "activateEntry(entry)" in launcher
    assert "api." not in launcher, "il foglio non parla con la rete (D5)"


def test_only_an_android_launch_closes_the_sheet() -> None:
    """Una app Android porta via il task; una Jenny App si apre *sopra* il
    foglio e Indietro ci riporta (1.7).

    Dal passo 6.3 la condizione è **doppia**: solo `android`, e solo se l'avvio
    è riuscito. Una riga stantia — pacchetto disinstallato o disabilitato fra il
    caricamento e il tocco — fallisce, e chiudere il foglio su quel fallimento
    lascerebbe davanti alla chat con un toast e senza il cassetto da cui
    riprovare.
    """
    body = _method(_src("mobile-launcher.js"), "_activate")
    assert "if (entry.kind !== 'android') return;" in body, (
        "le altre due specie non chiudono il foglio"
    )
    assert "this.close()" in body.split("if (entry.kind !== 'android') return;")[1], (
        "la chiusura deve restare nel solo ramo android"
    )
    assert "ok !== false" in body, (
        "senza l'esito, un avvio fallito chiude comunque il foglio: è il difetto "
        "di 6.3 con un passo in più"
    )


def test_usage_is_recorded_before_the_launch() -> None:
    """``launchAndroidApp`` porta via il task: dopo di lei non è detto che
    questo JS giri ancora."""
    body = _method(_src("mobile-launcher.js"), "_activate")
    assert body.index("this._usage.record(") < body.index("activateEntry(entry)")


# ── 4.5 — la semantica, dalla nascita ───────────────────────────────────────

def test_rows_are_options_of_a_listbox_not_buttons() -> None:
    """Qui la riga nasce `option` di un `listbox`, non `button`: `role="button"` su ogni riga non avrebbe modo di
    esprimere *quale* è selezionata, e chi legge lo schermo sentirebbe settanta
    pulsanti uguali con l'evidenziazione ridotta a un colore."""
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "setAttribute('role', 'option')" in body
    assert "setAttribute('role', 'button')" not in body
    assert "setAttribute('aria-selected', 'false')" in body
    # Tutte focalizzabili, non solo la selezionata: su Android il gesto di
    # scorrimento di TalkBack passa per gli elementi focalizzabili, e un
    # `roving tabindex` darebbe a Tab una sola fermata su tutta la lista.
    assert "setAttribute('tabindex', '0')" in body
    html = (ROOT / "jenny" / "templates" / "ui" / "officina.html").read_text(encoding="utf-8")
    assert 'role="listbox"' in html, "la lista non si dichiara"
    assert 'role="combobox"' in html, "il campo non governa la lista"


def test_the_selection_is_announced_without_moving_focus() -> None:
    """Il cuore del passo 4: le frecce muovono la selezione e il fuoco resta
    nel campo — è quello che tiene il campo scrivibile mentre si sceglie. Chi
    legge lo schermo lo sa solo grazie ad `aria-activedescendant`; senza,
    l'unica traccia della selezione sarebbe un bordo colorato."""
    body = _method(_src("mobile-launcher.js"), "_select")
    assert "aria-activedescendant" in body
    assert "aria-selected" in body
    assert ".focus(" not in body, (
        "spostare il fuoco a ogni freccia chiuderebbe la tastiera software e "
        "porterebbe via il cursore dal campo"
    )


def test_the_selected_row_is_scrolled_into_view_without_a_jump() -> None:
    """4.3: `block: 'nearest'` scorre quel tanto che basta. Con 'center' ogni
    passo sposterebbe la lista di mezza schermata."""
    body = _method(_src("mobile-launcher.js"), "_select")
    assert "scrollIntoView({ block: 'nearest' })" in body


def test_a_row_that_takes_focus_becomes_the_selection() -> None:
    """Chi arriva su una riga con Tab o col dito di TalkBack e preme ⏎ deve
    aprire *quella*, non quella evidenziata da una freccia di prima."""
    source = _src("mobile-launcher.js")
    assert "this.list?.addEventListener('focusin'" in source


# ── 4.1 — il type-ahead è quello già tarato, non una seconda copia ──────────

def test_the_type_ahead_guard_has_exactly_one_copy() -> None:
    """Le quattro condizioni erano tarate su questo hardware dentro
    `_maybeTypeAheadFocus`. Riscriverle nel cassetto avrebbe prodotto una
    seconda versione destinata a divergere sul caso raro — che qui è il caso
    vero: il keydown con `key` undefined delle tastiere fisiche."""
    guard = (ASSETS / "shared" / "type-ahead.js").read_text(encoding="utf-8")
    assert "e.key.length !== 1" in guard
    assert "e.key === ' '" in guard
    assert "e.metaKey || e.ctrlKey || e.altKey" in guard
    for name in ("mobile-chat.js", "mobile-launcher.js"):
        source = _src(name)
        assert "isTypeAheadKey" in source, f"{name} non usa la guardia condivisa"
        assert "e.key.length !== 1" not in source, (
            f"{name} si è riscritto la guardia invece di importarla"
        )


def test_the_sheet_yields_the_keys_to_whatever_is_above_it() -> None:
    """Il foglio resta aperto sotto una mini-app o sotto la scheda di una skill
    (1.7, 3.7): senza la guardia continuerebbe a rispondere a frecce e ⏎ da
    dietro un overlay. È il difetto di 1.9 rovesciato."""
    body = _method(_src("mobile-launcher.js"), "_ownsKeys")
    assert "hasOverlayAbove?.('launcher')" in body, (
        "senza il nome del livello il foglio si escluderebbe da solo: "
        "`present()` del proprio livello è vero mentre è a schermo"
    )
    app = _method(_src("mobile-app.js"), "hasOverlayAbove")
    assert "belowLayer" in app


# ── 4.4 — ⏎, ⇧⏎, Esc ────────────────────────────────────────────────────────

def test_shift_enter_opens_the_card_and_does_not_count_as_a_launch() -> None:
    """La scheda è dove si va per disinstallare o per capire cosa sia una voce:
    contarla farebbe salire in classifica proprio quelle di cui si dubita."""
    body = _method(_src("mobile-launcher.js"), "_activateSelected")
    assert "detailEntry(entry)" in body
    assert "_usage.record" not in body
    detail = _method(_src("shared/apps-actions.js"), "detailEntry")
    assert "showAndroidAppSheet" in detail
    assert "showJennyAppSheet" in detail
    # Niente `showSkillSheet`: le skill non entrano nel cassetto (non si
    # lanciano) e la scheda che le gestiva e' stata cancellata il 21/09/2026.
    assert "showSkillSheet" not in detail


def test_escape_and_back_clear_the_field_before_closing() -> None:
    """Esc non ha un handler proprio: `keyboard.register('escape')` lo manda in
    `handleHardwareBack()`, cioè nella catena dei livelli. Una decisione sola
    per due tasti che sul Titan 2 stanno entrambi sotto le dita."""
    body = _method(_src("mobile-launcher.js"), "dismiss")
    assert "this.search.value = ''" in body
    assert body.index("this.search.value = ''") < body.index("this.close()")


def test_home_dismounts_the_sheet_in_one_call() -> None:
    """Da quando `dismiss` fa due passi, il default `layer.close ||
    layer.dismiss` non basta più: Home smonta e basta, e senza un `close`
    proprio il conto di 1.8 passerebbe da una chiamata a due."""
    layers = _method(_src("mobile-app.js"), "_overlayLayers")
    launcher = layers[layers.index("name: 'launcher'"):]
    assert "close: () => { this.launcher.close(); }" in launcher
    # Home passa **solo** di lì. La seconda asserzione qui citava
    # `LauncherController.collapseToRoot`, che `goHome` non chiama mai: itera
    # `this.controllers`, e il foglio ne sta fuori di proposito (è un livello,
    # non una vista). Il metodo è stato rimosso; un test che lo nominava dava
    # per coperto un percorso inesistente.
    assert "collapseToRoot" not in _src("mobile-launcher.js"), (
        "il foglio non è in this.controllers: un collapseToRoot qui non lo "
        "chiamerebbe nessuno"
    )


# ── tre difetti visti girare sull'emulatore, e le loro guardie ──────────────

def test_reopening_the_sheet_starts_from_the_top_row() -> None:
    """Senza questo, ⏎ appena aperto lanciava la riga evidenziata l'altra volta
    — cioè avviava qualcosa che nessuno aveva scelto adesso. Osservato: aperto
    il foglio, la selezione era ancora su "Google" della sessione precedente."""
    body = _method(_src("mobile-launcher.js"), "open")
    assert "this._select(null)" in body, (
        "azzerare solo `_selectedKey` lascerebbe la riga di ieri marcata: la "
        "sua classe e il suo aria-selected stanno sul nodo, non nella chiave"
    )
    assert "this._selectionPinned = false" in body


def test_the_selection_follows_the_top_until_someone_moves_it() -> None:
    """Le app Android arrivano dopo le skill: una selezione *pinnata* fin dal
    primo disegno resta incollata a chi era in cima quando la lista era ancora
    a metà, e si finisce con la dodicesima riga evidenziata, fuori schermo.
    Osservato: `selected: app-creator` con `first: Camera`."""
    render = _method(_src("mobile-launcher.js"), "_renderList")
    assert "this._selectionPinned && this._rankedKeys.includes" in render
    typing = _method(_src("mobile-launcher.js"), "_onQueryChanged")
    assert "_selectionPinned = false" in typing, (
        "una query nuova è una domanda nuova: la risposta migliore torna in cima"
    )
    move = _method(_src("mobile-launcher.js"), "_moveSelection")
    assert "_selectionPinned = true" in move


def test_the_decorative_glyph_is_not_read_aloud() -> None:
    """I glifi Tabler sono caratteri della zona a uso privato dentro un font:
    senza `aria-hidden` il nome accessibile della riga di una skill comincia
    con un carattere di spazzatura — letto nell'albero di accessibilità della
    WebView, non dedotto. L'icona di una app Android non ha il problema: è una
    `<img alt="">`, che al nome non contribuisce."""
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "glyph.setAttribute('aria-hidden', 'true')" in body
    # Il glifo del server invece *porta* informazione: non si nasconde, si nomina.
    assert "server.setAttribute('aria-label'" in body


# ── la rotella ──────────────────────────────────────────────────────────────

def test_the_wheel_moves_the_selection_and_says_what_is_unverified() -> None:
    """Quali eventi produca la rotella del Titan 2 **non è accertato**, e
    sull'emulatore la rotella non c'è. Le due letture più probabili sono
    coperte entrambe (`wheel` qui, ↑↓ in `_onKeyDown`); ciò che non si può
    dire da qui va scritto dove chi legge il codice lo trova."""
    source = _src("mobile-launcher.js")
    body = _method(source, "_onWheel")
    assert "_moveSelection" in body
    assert "e.preventDefault()" in body
    assert "deltaMode" in body, (
        "una rotella che conta righe e una che conta pixel non si sommano"
    )
    assert "{ passive: false }" in source, (
        "senza passive:false il preventDefault non ferma lo scorrimento"
    )
    assert "non è accertato" in source, "l'incognita della rotella non è dichiarata"


# ── i18n ────────────────────────────────────────────────────────────────────

def test_no_hardcoded_strings_in_the_sheet() -> None:
    source = _src("mobile-launcher.js")
    for key in ("launcher.recent", "launcher.results", "launcher.noResults"):
        assert f"'{key}'" in source, f"{key} non usata"
    # Il placeholder e le etichette statiche stanno nell'HTML, non nel JS.
    html = (ROOT / "jenny" / "templates" / "ui" / "officina.html").read_text(encoding="utf-8")
    assert 'data-i18n-placeholder="launcher.searchPlaceholder"' in html
    assert 'data-i18n-aria="launcher.clearSearch"' in html


# ── passo 6 — i bordi ───────────────────────────────────────────────────────

def test_the_sheet_is_actually_in_the_page() -> None:
    """Nessun test lo diceva, e i contract dei passi 3-5 lo davano per scontato.

    Il registro dei livelli, l'ordine fra `miniapp` e `drawer`, `present` e
    `dismiss` sono coperti: ma tutti guardano il *controller*. Il foglio è fatto
    di nodi che stanno in `officina.html` — e `LauncherController` esce subito
    (`if (!this.sheet) return`) se non li trova, senza un errore. Cancellare il
    blocco HTML lascerebbe verdi tutti gli altri test e un pulsante che non apre
    niente.
    """
    html = (ROOT / "jenny" / "templates" / "ui" / "officina.html").read_text(encoding="utf-8")
    for node in ('id="launcher-sheet"', 'id="launcher-scrim"', 'id="launcher-list"',
                 'id="launcher-search"', 'id="launcher-title"', 'id="launcher-close"',
                 'id="launcher-handle-row"'):
        assert node in html, f"{node} manca da officina.html: il foglio non esiste più"
    # L'unico ingresso. Era lo slot Apps del dock; dal 20/09/2026 il dock ha
    # quattro voci e quello slot non c'è più, quindi la maniglia è la porta in
    # cima al cassetto Mani (v. CASSETTI in mobile-settings.js).
    impostazioni = _src("mobile-settings.js")
    assert "PORTE_LANCIO = { launcher:" in impostazioni, (
        "senza questo il foglio non si apre da nessuna parte"
    )
    # Vive *fuori* da `.app`: è ciò che gli permette di coprire il dock, di
    # restare fuori dall'inerzia che si applica allo sfondo, e di lasciarsi
    # sovrapporre da una mini-app aperta da lui senza trucchi di z-index.
    assert html.index('id="launcher-sheet"') > html.index('<div class="app"'), (
        "il foglio deve stare dopo `.app`, non dentro"
    )
    app = _src("mobile-app.js")
    assert "new LauncherController(this)" in app
    assert "name: 'launcher'" in _method(app, "_overlayLayers")


def test_the_manage_row_is_gone_with_the_screen_it_led_to() -> None:
    """La riga «Gestisci app e skill» portava alla scheda «App».

    Quella scheda e' stata cancellata il 21/09/2026: il cassetto e' l'unico
    posto, e quel che la riga prometteva — disinstallare, info app — si fa col
    **tocco lungo** su una riga. Una porta che non si apre e' peggio di una
    porta che manca, ed e' il motivo per cui se n'e' andata invece di restare
    disabilitata.
    """
    for doc in ("officina.html", "index.html"):
        html = (ROOT / "jenny" / "templates" / "ui" / doc).read_text(encoding="utf-8")
        assert "launcher-manage" not in html, doc
    launcher = _src("mobile-launcher.js")
    assert "manageBtn" not in _senza_commenti_js(launcher)
    assert "_openManager" not in _senza_commenti_js(launcher)


def test_the_three_empty_states_are_three_different_sentences() -> None:
    """6.2: "non è ancora arrivato", "non si è potuto leggere" e "non c'è
    niente" sono tre risposte diverse — aspetta, riprova, installa qualcosa.
    Nella scheda di oggi sono la stessa frase, ed è il limite scritto in
    `docs/using/app-launcher.md`."""
    render = _method(_src("mobile-launcher.js"), "_renderList")
    for key in ("launcher.loading", "launcher.error", "launcher.empty", "launcher.noResults"):
        assert f"'{key}'" in render, f"{key} non usata: uno stato vuoto si è confuso con un altro"
    assert "isLoadingLists()" in render and "listsFailed()" in render


def test_a_broken_bridge_is_not_an_empty_phone() -> None:
    """Il caso che il documento denuncia, e che gli stati vuoti da soli **non**
    coprono: il ponte nativo tace, skill e Jenny App arrivano tutte, e mancano
    solo le app del telefono. La lista non è vuota — nessuno stato vuoto
    comparirebbe — e l'unico segno sarebbe un cassetto che non trova Telefono.

    Serve che l'informazione esista lungo tutta la catena: il gateway la
    dichiara, il controller la tiene separata da "caricato", il foglio la
    mostra.
    """
    server = (ROOT / "jenny" / "webui" / "android_apps_api.py").read_text(encoding="utf-8")
    assert '{"apps": [], "error": "unavailable"}' in server, (
        "senza il campo, la risposta di un ponte rotto è identica a quella di "
        "un telefono senza app"
    )
    apps = _src("shared/apps-source.js")
    android = _method(apps, "loadAndroidApps")
    assert "data.error" in android, "il campo arriva e viene buttato"
    assert "announceRemovals && apps && !failed" in android, (
        "una lista vuota per guasto annuncerebbe come disinstallate tutte le "
        "app del telefono in un colpo"
    )
    assert "listsFailed()" in apps
    launcher = _src("mobile-launcher.js")
    assert "_syncStatus" in launcher
    html = (ROOT / "jenny" / "templates" / "ui" / "officina.html").read_text(encoding="utf-8")
    assert 'id="launcher-status"' in html
    # Fuori dalla lista: i figli di un `listbox` sono `option`, e un avviso là
    # dentro si annuncerebbe come una voce da aprire.
    assert html.index('id="launcher-status"') < html.index('id="launcher-list"')


def test_a_failed_launch_says_something() -> None:
    """6.3: prima qui c'era un `catch` vuoto commentato "best effort", e un
    avvio fallito non produceva nessun segno — indistinguibile da un tocco non
    registrato. L'informazione c'era già: l'endpoint risponde 404."""
    body = _method(_src("shared/apps-actions.js"), "launchAndroidApp")
    assert "showToast" in body
    assert "apps.launchFailed" in body
    assert "return false" in body and "return true" in body, (
        "senza l'esito il cassetto non può decidere se chiudersi"
    )
    activate = _method(_src("shared/apps-actions.js"), "activateEntry")
    assert "return this.launchAndroidApp(entry.id)" in activate, (
        "l'esito va restituito, non lasciato cadere"
    )


def test_the_step_six_chrome_gets_out_of_the_way_of_the_keyboard() -> None:
    """La cornice `.compact` esiste per far entrare **una riga intera** (5.5),
    e i due elementi del passo 6 se la riprendono tutta: misurato con la
    tastiera su, la riga «Gestisci» costa 30 px su 46 di lista e l'avviso 28 —
    con l'avviso a schermo la lista scende a 14 px, cioè zero righe intere. È
    il difetto che 5.5 ha chiuso, reintrodotto da un bordo."""
    css = _src("mobile-style.css")
    rule = re.search(
        r"\.launcher-sheet\.compact \.launcher-manage,\s*\n"
        r"\.launcher-sheet\.compact \.launcher-status \{ display: none; \}", css)
    assert rule, "in `.compact` la riga «Gestisci» e l'avviso devono sparire"


def test_the_new_step_six_strings_exist_in_both_locales() -> None:
    """6.4 — niente testo cablato, e la parità si legge, non si guarda."""
    import json

    i18n_dir = ASSETS / "i18n"
    expected = {"launcher.manage", "launcher.error", "launcher.loadFailed",
                "launcher.retry", "apps.launchFailed"}
    for locale in ("it", "en"):
        data = json.loads((i18n_dir / f"{locale}.json").read_text(encoding="utf-8"))
        flat = {f"{a}.{b}" for a, group in data.items() if isinstance(group, dict)
                for b in group}
        assert expected <= flat, f"chiavi mancanti in {locale}.json: {sorted(expected - flat)}"


def test_the_search_field_does_not_autofocus() -> None:
    """D6: su un telefono con tastiera software l'autofocus alzerebbe la
    tastiera e si mangerebbe il foglio."""
    html = (ROOT / "jenny" / "templates" / "ui" / "officina.html").read_text(encoding="utf-8")
    field = re.search(r'<input class="launcher-search".*?>', html, re.S)
    assert field, "campo di ricerca non trovato"
    assert "autofocus" not in field.group(0)
    open_body = _method(_src("mobile-launcher.js"), "open")
    assert "this.search.focus" not in open_body


# ── l'invariante: la lista fuori dalla fascia della gesture ─────────────────


def test_the_gesture_margin_chain_is_unbroken() -> None:
    """Il margine che tiene la lista fuori dalla fascia di home attraversa
    quattro strati — metodo nativo, ponte JS, custom property, CSS — e nessuno
    di essi conosce gli altri: sono legati solo dai nomi. Rinominarne uno da
    una parte sola non rompeva nessun test, e il difetto sarebbe comparso solo
    su un dispositivo in navigazione a gesture, come otto pixel di lista dentro
    la zona in cui il tocco va alla shell (v. il passo 5 del piano: sono
    esattamente quegli otto pixel a separare "scorre" da "l'interfaccia
    collassa"). Questo test è il nodo che li tiene insieme.
    """
    kotlin = (
        ROOT / "android/app/src/main/java/com/flagdizero/jenny/MainActivity.kt"
    ).read_text(encoding="utf-8")
    assert "fun getBottomGestureInset()" in kotlin
    # Raggiunto solo per reflection: senza l'annotazione la WebView non lo vede,
    # e R8 in release non avrebbe motivo di tenerlo.
    head = kotlin[: kotlin.index("fun getBottomGestureInset()")]
    assert head.rstrip().endswith("@JavascriptInterface"), (
        "il metodo del ponte deve portare @JavascriptInterface"
    )

    js = _src("mobile-launcher.js")
    assert "native.getBottomGestureInset()" in js, "il consumatore JS chiama il metodo nativo"
    assert "'--gesture-inset-bottom'" in js, "e ne scrive il valore nella custom property"

    css = _src("mobile-style.css")
    assert "padding-bottom: var(--gesture-inset-bottom, 0px)" in css, (
        "e il foglio la consuma come proprio padding inferiore"
    )


def test_the_margin_rounds_away_from_the_gesture_zone() -> None:
    """`round` sbaglia per difetto metà delle volte, e per difetto vuol dire
    dentro la fascia: a dpr 3, 25 px nativi sono 8,33 px CSS."""
    body = _method(_src("mobile-launcher.js"), "_syncGestureInset")
    assert "Math.ceil(px / dpr)" in body
    assert "Math.round(px / dpr)" not in body


def test_the_mascot_goes_under_the_scrim_with_the_sheet_open() -> None:
    """La mascotte vive dentro `#app`, che il foglio rende `inert` — ma `inert`
    toglie fuoco e tocchi, non l'impilamento: a z-index 120 resterebbe dipinta
    sopra il foglio (100) e lo scrim (99), sulle righe. Difetto visto sul Titan
    2, non sull'emulatore, dove le due cose non si sovrapponevano.
    """
    js = _method(_src("mobile-launcher.js"), "_setBackgroundInert")
    assert "classList.toggle('launcher-open', on)" in js, (
        "il segno che fa scendere la mascotte deve seguire l'inerzia dello sfondo"
    )
    css = _src("mobile-style.css")
    assert ":root.launcher-open .jenny-duo { z-index: 98; }" in css
    # 98 deve stare *sotto* lo scrim, o la correzione non serve a niente.
    scrim = re.search(r"\.launcher-scrim\s*\{([^}]*)\}", css)
    assert scrim and "z-index: 99" in scrim.group(1)


def test_the_app_drawer_keeps_a_handle_after_the_dock_shrank() -> None:
    """Lo slot Apps del dock apriva il cassetto. Dal 20/09/2026 il dock ha
    quattro voci e quello slot non esiste: senza una maniglia nuova il foglio
    resterebbe **vivo e irraggiungibile** — il modo piu' silenzioso di perdere
    una schermata, perche' tutti gli altri banchi restano verdi.

    La maniglia e' una porta in cima al cassetto Mani, ed e' una porta
    speciale: non cambia vista, apre il foglio.
    """
    src = _src("mobile-settings.js")
    assert "PORTE_LANCIO = { launcher: (app) => app.openLauncher() }" in src
    # `porte` e' passata da elenco per cassetto a mappa gruppo -> porte: le
    # destinazioni stanno ora in fondo al gruppo che le riguarda, non in cima
    # al cassetto. La maniglia resta dentro Mani, cambia solo dove.
    m = re.search(r"mani: \{\s*sezioni: \[[^\]]*\],\s*porte: \{([^}]*)\}", src)
    assert m and "'launcher'" in m.group(1), "il cassetto Mani non porta piu' al foglio"

    app = _src("mobile-app.js")
    # E l'aggancio del dock resta **uno solo**: un secondo `forEach` con un
    # `click` rimetterebbe in piedi il vecchio comportamento accanto al nuovo.
    handlers = re.findall(
        r"\.dock-item\[data-mode\]'\)\.forEach\(item => \{\s*item\.addEventListener\('click'", app)
    assert len(handlers) == 1, f"un solo aggancio al click del dock, trovati {len(handlers)}"
    assert "this.openLauncher()" in app


def test_the_dock_is_a_console_and_three_faculties() -> None:
    """Console, Cervello, Mani, Memoria. Erano cinque per sottosistema; adesso
    sono quattro per domanda. L'ordine del DOM e' anche quello del carosello
    (``_visibleModes``), quindi e' un contratto e non una preferenza grafica.

    ``cervello``, ``mani`` e ``memoria`` non hanno una vista propria: sono tre
    cassetti di ``view-settings``, e il guscio lo sa da una tabella sola.
    """
    html = (ROOT / "jenny/templates/ui/officina.html").read_text(encoding="utf-8")
    nav = html[html.index('<nav class="dock"'):html.index("</nav>")]
    modes = [m for m in re.findall(r'data-mode="([a-z]+)"', nav) if m != "onboarding"]
    assert modes == ["chat", "cervello", "mani", "memoria"], modes

    # La tabella sta accanto a `CASSETTI`, non nel guscio: serve anche
    # all'intestazione (`mobile-header.js::_mount`), e la copia che mancava li'
    # lasciava i tre cassetti senza titolo.
    assert (
        "export const VISTA_DI = { cervello: 'settings', mani: 'settings', memoria: 'settings' };"
        in _src("mobile-settings.js")
    )
    app = _src("mobile-app.js")
    # Un controller solo per i tre cassetti: tre istanze vorrebbero dire tre
    # `/api/settings` e due copie che invecchiano mentre guardi la terza.
    assert "this._impostazioni ||= new SettingsController()" in app


def test_the_sheet_itself_shows_no_focus_ring() -> None:
    """Il foglio prende il fuoco all'apertura per fare da àncora a TalkBack e ai
    tasti, ma ha `tabindex="-1"`: da tastiera non ci si arriva, quindi l'anello
    non segnala nulla e si vede soltanto. I controlli *dentro* lo tengono."""
    html = (ROOT / "jenny/templates/ui/officina.html").read_text(encoding="utf-8")
    sheet = re.search(r'<div class="launcher-sheet"[^>]*>', html).group(0)
    assert 'tabindex="-1"' in sheet, "se diventasse raggiungibile con Tab, l'anello servirebbe"
    css = _src("mobile-style.css")
    assert ".launcher-sheet:focus,\n.launcher-sheet:focus-visible { outline: none; }" in css


def test_no_skills_in_the_drawer() -> None:
    """Il cassetto è un lanciatore, e le skill non si lanciano: toccarne una
    apre una scheda o un file. Mescolarle alle app rifaceva, un livello più in
    là, il difetto 01 del rilievo — un solo elenco per nature diverse. Restano
    nella scheda Apps; dove vadano davvero è ancora da decidere.
    """
    body = _method(_src("shared/apps-source.js"), "launcherEntries")
    assert "for (const skill of this.skills)" not in body
    assert "kind: 'skill'" not in body
    assert "skill:${skill.name}" not in body
    # E il foglio non deve avere più un ramo per una specie che non arriva.
    assert "'skill'" not in _method(_src("mobile-launcher.js"), "_buildRow")


# Copiato da `test_casa_tu_contract.py`: unisce i corpi di tutte le regole che
# nominano il selettore, gruppi compresi — guardarne una sola dice «non c'e'».
def _rule(css: str, selector: str) -> str:
    """Tutto cio' che il foglio dichiara per *selector*, gruppi compresi.

    Unisce i corpi invece di prendere il primo: quelle due proprieta' arrivano
    da due regole diverse — il gruppo che mette davanti le schede e la regola
    che veste quella singola — e guardarne una sola dice «non c'e'».
    """
    corpi = []
    for selettori, corpo in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        nomi = {s.strip().splitlines()[-1].strip() for s in selettori.split(",") if s.strip()}
        if selector in nomi:
            corpi.append(corpo)
    return "\n".join(corpi)


def _senza_commenti_html(src: str) -> str:
    """Il testo senza i `<!-- -->`.

    Serve perche' i commenti di questi file *nominano* apposta il codice che
    non c'e' piu' (e' cosi' che si spiega una rimozione), e un banco che
    grepasse il file intero leggerebbe la spiegazione come se fosse la cosa
    spiegata.
    """
    return re.sub(r"<!--.*?-->", "", src, flags=re.S)


def _senza_commenti_js(src: str) -> str:
    """Idem per `/* */` e `//`. Grezzo — una stringa che contiene `//` ci va di
    mezzo — e va bene: si usa solo per cercare identificatori che *non* devono
    esistere, dove un falso negativo e' impossibile e un falso positivo si vede
    subito."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def test_the_drawer_is_reachable_from_every_view() -> None:
    """**L'invariante, non l'implementazione.** Il cassetto deve avere almeno un
    ingresso che esiste in *ogni* vista.

    Storia, perché la regola è stata scritta tre volte e due erano sbagliate:

    1. C'era un pulsante nel composer. Il composer però vive solo nella vista
       chat, quindi da Workspace, Apps o Impostazioni il cassetto non si apriva.
    2. Fu tolto e sostituito dallo slot «Apps» del dock (`data-opens="launcher"`),
       che c'è ovunque. Il banco di allora si chiamava
       `test_the_composer_has_no_launcher_button` e difendeva proprio quello.
    3. Il passo 0 del rimaneggiamento ha portato il dock a quattro cassetti e
       **si è portato via anche quello slot**, lasciando il ramo che lo gestiva
       come codice morto. Da lì il cassetto si raggiungeva solo da Mani →
       «Cassetto delle app»: tre tocchi per la cosa che un launcher fa più
       spesso, e nessun banco se n'era accorto — perché difendevano la forma
       («niente pulsante») e non lo scopo («si apre da ovunque»).

    Oggi gli ingressi sono due e fanno cose diverse: la porta dentro Mani è
    quella che c'è **sempre**, ed è lei che tiene l'invariante; il pulsante nel
    composer è la scorciatoia dov'è più frequente. Il difetto del punto 1 non
    torna proprio perché il primo esiste.
    """
    officina = (ROOT / "jenny/templates/ui/officina.html").read_text(encoding="utf-8")
    impostazioni = _src("mobile-settings.js")

    # L'ingresso che vale da ogni cassetto: la porta dentro Mani.
    assert re.search(r"porte: \{[^}]*'launcher'", impostazioni), (
        "tolta la porta, il cassetto torna raggiungibile solo dalla chat"
    )
    assert "PORTE_LANCIO = { launcher:" in impostazioni

    # La scorciatoia dalla chat, nei due gusci.
    assert 'id="btn-launcher"' in officina
    casa = (ROOT / "jenny/templates/ui/index.html").read_text(encoding="utf-8")
    assert 'id="casa-drawer"' in casa

    # E i due pulsanti devono essere agganciati, non decorativi.
    assert "getElementById('btn-launcher')" in _src("mobile-app.js")
    assert "getElementById('casa-drawer')" in _src("casa-app.js")

    # Il modulo del cassetto non conosce gli id dei due gusci: li aggancia chi
    # li possiede. Senza questo il foglio saprebbe di stare in due case.
    assert "btn-launcher" not in _src("mobile-launcher.js")
    assert "casa-drawer" not in _src("mobile-launcher.js")


def test_the_dead_dock_branch_is_gone() -> None:
    """Il ramo che apriva il cassetto dallo slot del dock non esiste più.

    `data-opens` non è in nessuno dei due documenti dal passo 0: il ramo che lo
    leggeva è rimasto lì a non fare niente per quattro commit. Un `if` su un
    attributo che nessun elemento porta è il modo in cui una funzionalità
    sparisce senza che nulla diventi rosso.
    """
    for doc in ("officina.html", "index.html"):
        html = (ROOT / "jenny/templates/ui" / doc).read_text(encoding="utf-8")
        assert "data-opens" not in _senza_commenti_html(html), doc
    assert "dataset.opens" not in _senza_commenti_js(_src("mobile-app.js"))

def test_what_the_sheet_hides_at_runtime_really_disappears() -> None:
    """`[hidden]` e' a specificita' zero: una classe con `display` lo scavalca.

    Il banco gemello in `test_casa_tu_contract.py` guarda gli elementi che
    nascono `hidden` **nel markup**. Questo guarda l'altra meta', che e' la
    piu' insidiosa: quelli che il JS nasconde **a runtime**. Li' il difetto non
    si vede leggendo l'HTML — l'attributo non c'e' finche' il codice non lo
    mette — e a schermo il risultato e' un elemento che si crede nascosto e non
    lo e'.

    Preso sul banco il 20/09/2026: portando il cassetto in casa, «Gestisci app
    e skill» si nasconde perche' li' non ha dove portare
    (`_manageAvailable()`), `el.hidden` valeva `true`, e la riga si vedeva
    lo stesso — un bottone che prometteva una schermata inesistente. Quarta
    volta per questa stessa classe di difetto in questo progetto.
    """
    js = _src("mobile-launcher.js")
    officina = (ROOT / "jenny/templates/ui/officina.html").read_text(encoding="utf-8")
    casa = (ROOT / "jenny/templates/ui/index.html").read_text(encoding="utf-8")
    css = _src("mobile-style.css")

    # I campi che il cassetto nasconde a runtime, risaliti al loro nodo.
    campi = set(re.findall(r"this\.(\w+)\.hidden\s*=", js))
    assert campi, "nessun `.hidden =` trovato: il banco guarda il posto sbagliato"

    guasti = []
    for campo in sorted(campi):
        m = re.search(rf"this\.{campo}\s*=\s*document\.getElementById\('([^']+)'\)", js)
        assert m, f"non risalgo al nodo di this.{campo}"
        nodo_id = m.group(1)
        for doc, nome in ((officina, "officina.html"), (casa, "index.html")):
            tag = re.search(rf'<[a-z]+[^>]*id="{re.escape(nodo_id)}"[^>]*>', doc)
            if not tag:
                continue
            classi = re.search(r'class="([^"]+)"', tag.group(0))
            if not classi:
                continue
            for classe in classi.group(1).split():
                corpo = _rule(css, f".{classe}")
                if not re.search(r"display:\s*(?!none)", corpo):
                    continue
                if f".{classe}[hidden]" not in css:
                    guasti.append(f"{classe} ({nome})")

    assert not guasti, (
        f"il JS li nasconde ma il CSS li riaccende: {sorted(set(guasti))} "
        "— serve una regola `[hidden]` che batta il loro `display`"
    )

def test_nothing_in_the_casa_shows_a_hardcoded_string() -> None:
    """**La casa non ha una passata generica sui `data-i18n-*`.**

    L'officina sì (`MobileApp._applyStaticTranslations`, che spazza tutto il
    documento), e per questo il markup del foglio ha sempre potuto portarsi
    dietro dei segnaposto italiani: qualcuno li riscriveva. In casa quella
    passata non esiste — non ne aveva mai avuto bisogno, perché il suo markup
    non conteneva nemmeno un `data-i18n` — e portandoci dentro il foglio ci
    sono arrivate dieci stringhe fisse.

    Visto sul telefono il 20/09/2026, lingua su inglese: il titolo diceva
    «MOST USED» (quello lo scrive il JS) e sotto il campo diceva «Cerca
    un'app…». Metà schermata in una lingua e metà nell'altra.

    Il banco chiede che **ogni chiave `data-i18n*` del markup della casa sia
    scritta da qualcuno**: dal cassetto, che ora ha la sua passata, o dal
    guscio. Una chiave che nessuno scrive è un segnaposto che resta a schermo.
    """
    casa = (ROOT / "jenny/templates/ui/index.html").read_text(encoding="utf-8")
    # I moduli che possiedono dei nodi nel markup della casa. `apps-actions.js`
    # e' entrato nell'elenco il 21/09/2026 con i due fogli per-app, che sono
    # arrivati dall'officina portandosi dietro le sue parole.
    scrittori = (
        _src("mobile-launcher.js") + _src("casa-app.js")
        + _src("shared/apps-actions.js")
    )

    chiavi = set(re.findall(r'data-i18n(?:-[a-z]+)?="([^"]+)"', casa))
    assert chiavi, "nessuna chiave nel markup della casa: il banco guarda il posto sbagliato"

    orfane = [k for k in sorted(chiavi) if f"'{k}'" not in scrittori]
    assert not orfane, (
        f"queste chiavi nessuno le scrive, quindi a schermo resta il segnaposto "
        f"del markup: {orfane}"
    )

def test_the_static_strings_are_written_when_the_sheet_opens_not_at_boot() -> None:
    """**Il difetto che ho fatto io correggendo il difetto di prima.**

    `i18n.load()` è asincrona. Nel costruttore del cassetto — che gira al boot,
    con gli altri pezzi permanenti del guscio — le traduzioni non sono ancora
    arrivate e `i18n.t('x')` restituisce `'x'`. Ci avevo messo
    `_applyStaticTranslations()` il 20/09/2026, e sul telefono il campo di
    ricerca diceva **«launcher.searchPlaceholder»**: peggio del segnaposto
    italiano da cui si scappava, e su una schermata che si guarda davvero.

    Il file lo diceva già, due righe sopra, a proposito del primo disegno:
    «al boot le traduzioni non sono ancora arrivate e disegnare adesso vorrebbe
    dire scrivere le chiavi grezze». La regola vale per tutto ciò che legge
    `i18n.t()`, non solo per la lista.

    Quindi: si scrive all'apertura, quando le traduzioni ci sono di sicuro, e a
    ogni cambio di lingua. Mai nel costruttore.
    """
    js = _src("mobile-launcher.js")

    # La chiamata *diretta* nel costruttore e' quella sbagliata. Dentro il
    # callback di `onLocaleChange` ci sta di diritto — gira dopo — e si
    # distinguono dall'indentazione: quattro spazi il corpo del costruttore,
    # sei dentro la lambda. I commenti si tolgono prima: il costruttore nomina
    # il metodo per spiegarsi, e la spiegazione non e' una chiamata.
    costruttore = _senza_commenti_js(_method(js, "constructor"))
    assert "\n    this._applyStaticTranslations();" not in costruttore, (
        "nel costruttore i18n non ha ancora caricato: scriverebbe le chiavi grezze"
    )

    assert "_applyStaticTranslations();" in _method(js, "open"), (
        "senza la chiamata in open() il markup resta nella lingua in cui è scritto"
    )

    # E al cambio di lingua, altrimenti cambiarla lascia indietro questi nodi.
    dopo = js[js.index("i18n.onLocaleChange("):]
    assert "_applyStaticTranslations()" in dopo[:400]


def test_the_drawer_only_calls_methods_its_collaborators_have() -> None:
    """**Il difetto che ha rotto il cassetto il 21/09/2026.**

    Spostando i dati fuori dalla scheda «App» ho ribattezzato
    `isLoadingLists()` in `isLoading()` e ho lasciato indietro l'unico
    chiamante. Il cassetto si apriva — `isOpen()` tornava vero, il foglio
    prendeva la sua classe — e poi moriva a meta' disegno con
    `apps.isLoadingLists is not a function`: a schermo un foglio che sale e
    resta vuoto.

    Nessun banco lo vedeva. `test_no_ghost_methods_contract` guarda le chiamate
    `this._x()` **dentro un oggetto su se stesso**; questa e' l'altra meta': una
    chiamata su un **collaboratore**, dove il nome sta in un file e il metodo in
    un altro. E' la classe di difetto che nasce da ogni rinomina fatta a meta'.

    Il controllo e' grezzo apposta e puo' sbagliare in un verso solo — verso il
    falso allarme — e si zittisce aggiungendo il metodo, non allentando la
    regola.
    """
    import re as _re

    def metodi(percorso: str) -> set[str]:
        src = _src(percorso)
        return set(_re.findall(r"^  (?:async )?([A-Za-z][A-Za-z0-9]*)\s*\(", src, _re.M))

    offerti = metodi("shared/apps-source.js") | metodi("shared/apps-actions.js")
    assert "launcherEntries" in offerti, "il banco sta leggendo i file sbagliati"

    launcher = _src("mobile-launcher.js")
    # I tre modi in cui il foglio nomina i suoi due collaboratori.
    chiamate = set(_re.findall(
        r"(?:this\._apps|this\._azioni|\bapps)\??\.([A-Za-z][A-Za-z0-9]*)\(", launcher))
    assert chiamate, "nessuna chiamata trovata: il banco guarda il posto sbagliato"

    fantasmi = sorted(chiamate - offerti)
    assert not fantasmi, (
        f"il cassetto chiama metodi che ne' AppsSource ne' AppsActions hanno: "
        f"{fantasmi}. Il file resta valido, la suite verde, e il foglio si apre "
        f"vuoto al primo disegno."
    )
