"""Chi si sospende su un ``await`` deve poter scoprire di essere stato superato.

I token che c'erano (``_loadToken``, ``_navToken``) rispondono a una domanda
sola: *è partito un altro caricamento della stessa sezione?* Sono ciechi
all'altra, che sul telefono capita di continuo: *l'utente ha lasciato la
sezione mentre aspettavo?* Fra un tap sul dock e la risposta del gateway ci
stanno comodamente secondi, e nel frattempo la continuazione riprende e scrive
stato globale — il titolo dell'header (che è uno solo, e a quel punto appartiene
a un'altra vista), i drawer, la history, e nel caso peggiore apre una modale che
scrive in ``known_hosts``.

Il contratto di questa ondata: ogni controller espone un contatore di
generazione ``_gen``, incrementato in ``deactivate()``; ogni continuazione lo
cattura **prima** del primo await ed esce se è cambiato. In più le sorgenti di
caricamento sono una sola per sezione: due chiamanti che caricano la stessa
vista non sono un raddoppio di traffico, sono due render concorrenti.

Asserzioni sul sorgente, nello stile di ``test_back_navigation_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "mobile-app.js"
WIKI_JS = ASSETS / "mobile-wiki.js"
GRAPH_JS = ASSETS / "mobile-graph.js"
HEADER_JS = ASSETS / "mobile-header.js"
SETTINGS_JS = ASSETS / "mobile-settings.js"
TELEGRAM_JS = ASSETS / "shared" / "telegram-pairing.js"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(source: str) -> str:
    """Sorgente senza commenti: qui i commenti *citano* il difetto che descrivono
    (``loadGraph``, ``loadSettings()``…), quindi un'asserzione di assenza li
    troverebbe e fallirebbe su una spiegazione corretta."""
    return re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def _method(source: str, name: str) -> str:
    """Corpo di un metodo di classe (indentazione a 2 spazi, ``async`` opzionale)."""
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


# ── Il pattern condiviso ───────────────────────────────────────────────


def test_every_controller_with_async_loads_bumps_a_generation_on_leave() -> None:
    """Un contatore per controller, incrementato dove la sezione viene lasciata.

    Senza, l'unico segnale disponibile è il token di caricamento, che non cambia
    quando si esce: la continuazione si crede ancora l'ultima — e lo è — solo che
    la sua sezione non è più a schermo.
    """
    for path in (WIKI_JS, GRAPH_JS, SETTINGS_JS):
        source = _src(path)
        assert "this._gen = 0;" in source, f"{path.name}: manca il contatore di generazione"
        assert "this._gen++;" in _method(source, "deactivate"), (
            f"{path.name}: deactivate() non incrementa la generazione"
        )
        assert "_stale(" in source, f"{path.name}: nessuna guardia che legga la generazione"


# ── #5 · WikiController.init() non naviga ──────────────────────────────


def test_the_wiki_init_only_loads_config() -> None:
    """``init()`` ri-derivava la vista dalla query string **dopo** il suo await.

    Chi ci aveva appena portati in wiki fa ``switchMode('wiki', false)`` e subito
    dopo, sincronamente, ``loadHome(true)``. La continuazione di ``init()``
    arrivava più tardi, chiamava a sua volta ``load*`` e con essa incrementava
    ``_loadToken``: la ``loadHome(true)`` del chiamante veniva invalidata prima
    di arrivare alla propria ``pushNav``. A schermo la pagina giusta, dietro
    nessuna entry — cioè un Indietro che salta fuori dalla sezione.
    """
    wiki = _src(WIKI_JS)
    init = _method(wiki, "init")
    assert "loadHome" not in init and "loadWikiPage" not in init, (
        "init() naviga di nuovo: la prima vista non è affare suo"
    )
    assert "window.location.search" not in init, "init() non deve più leggere la query string"

    # La prima vista, se nessun chiamante l'ha scelta, la decide activate().
    assert "this._loadInitialView();" in _method(wiki, "activate")
    initial = _method(wiki, "_loadInitialView")
    assert "if (this._settled || this._inFlightGen === this._gen) return;" in initial, (
        "senza questa guardia activate() rifà la navigazione già fatta dal chiamante"
    )
    assert "window.location.search" in initial
    for name in ("loadHome", "loadWikiPage"):
        body = _method(wiki, name)
        assert "this._inFlightGen = gen;" in body, (
            f"{name} non segnala il caricamento in volo: activate() ne farebbe un secondo"
        )
        assert "this._settled = true;" in body, (
            f"{name} non segnala di aver disegnato: al rientro nella sezione activate() "
            f"ricaricherebbe una vista già a schermo"
        )


def test_an_abandoned_first_load_is_retried_on_re_entry() -> None:
    """Il contrappasso della guardia: un caricamento *condannato* non deve
    trattenerla.

    Uscire dalla wiki mentre la prima pagina è ancora in volo incrementa la
    generazione, e quella continuazione non disegnerà mai. Se `_loadInitialView`
    si accontentasse di "qualcuno ha già iniziato", al rientro non ricaricherebbe
    e la sezione resterebbe sul suo "Caricamento…" per il resto della sessione.
    Per questo la guardia confronta la *generazione* del caricamento in volo, non
    la sua semplice esistenza.
    """
    initial = _method(_src(WIKI_JS), "_loadInitialView")
    assert "this._inFlightGen === this._gen" in initial
    assert "this._inFlightGen)" not in initial and "if (this._inFlightGen)" not in initial


# ── #6 · le continuazioni della wiki ───────────────────────────────────


def test_the_wiki_guard_is_checked_after_every_await_not_once() -> None:
    """Il guard c'era, ma veniva controllato una volta sola.

    Restavano scoperti — verificato riga per riga — ``pushNav``,
    ``_renderLatex``, ``_renderMermaid`` e soprattutto ``loadTree``/
    ``loadAudits``, che di token non ne avevano affatto: la loro fetch è
    separata, e la risposta della pagina *vecchia* riempiva i drawer sopra
    quelli della pagina nuova. (``setTitle`` invece era già dentro la zona
    protetta: l'audit iniziale su questo punto era sbagliato.)
    """
    wiki = _src(WIKI_JS)
    stale = _method(wiki, "_stale")
    assert "token !== this._loadToken" in stale and "gen !== this._gen" in stale, (
        "la guardia deve rispondere a entrambe le domande: superato, e sezione lasciata"
    )

    for name in ("loadTree", "loadAudits"):
        signature = re.search(rf"async {name}\(([^)]*)\)", wiki)
        assert signature, f"{name} non trovato"
        args = signature.group(1)
        assert "token = this._loadToken" in args and "gen = this._gen" in args, (
            f"{name} deve ricevere il token del caricamento che lo ha chiesto "
            f"(col default per i chiamanti esterni)"
        )
        assert "if (this._stale(token, gen)) return;" in _method(wiki, name), (
            f"{name} scrive nel drawer senza controllare di essere ancora attuale"
        )

    for name in ("loadHome", "loadWikiPage"):
        body = _method(wiki, name)
        assert "this.loadTree(" in body and "token, gen)" in body, (
            f"{name} non passa il proprio token ai drawer"
        )
        head = body.partition("pushNav")[0]
        assert "loadAudits(" in head, f"{name}: ordine inatteso, il test va rivisto"
        between = head[head.index("loadAudits("):]
        assert "this._stale(token, gen)" in between, (
            f"{name}: pushNav impila una entry senza ricontrollare dopo gli await dei drawer"
        )


def test_the_lazily_loaded_mermaid_renderer_can_be_superseded() -> None:
    """``ensureVendor`` scarica 3,2 MB: lì dentro ci sta una navigazione intera,
    e al ritorno i ``pre.mermaid-block`` catturati sono nodi staccati."""
    wiki = _src(WIKI_JS)
    body = _method(wiki, "_renderMermaid")
    assert "async _renderMermaid(token = this._loadToken, gen = this._gen)" in wiki
    assert body.index("ensureVendor(") < body.index("if (this._stale(token, gen)) return;")
    assert body.count("this._stale(token, gen)") >= 2, (
        "anche la promise di mermaid.render riscrive il blocco: va guardata"
    )


# ── N10 · il titolo appartiene a una modalità ──────────────────────────


def test_the_view_title_refuses_writers_from_another_mode() -> None:
    """``titleEl`` viene ripuntato solo da ``setMode``, che ``switchMode`` chiama
    **prima** di ``deactivate``/``activate``: un caricamento lento della sezione
    che si sta lasciando riscriveva il titolo di quella di destinazione.

    Intermittente, e per questo insidioso: chat e onboarding non hanno mount, lì
    ``titleEl`` è null e non si vede niente.
    """
    header = _src(HEADER_JS)
    body = _method(header, "setTitle")
    assert "setTitle(title, ownerMode = null)" in header
    assert "if (ownerMode && ownerMode !== this.currentMode) return;" in body


def test_every_async_title_writer_declares_which_mode_it_belongs_to() -> None:
    for js in ASSETS.rglob("*.js"):
        # `vendor/` si esclude per path, non per basename: un domani
        # `assets/**/mermaid.min.js` non deve auto-esentarsi.
        if js.name == HEADER_JS.name or "vendor" in js.relative_to(ASSETS).parts:
            continue
        for lineno, line in enumerate(_src(js).splitlines(), 1):
            if ".setTitle(" not in line:
                continue
            assert re.search(r"\.setTitle\(.+,\s*'[a-z]+'\)", line), (
                f"{js.name}:{lineno} scrive il titolo senza dichiarare la modalità proprietaria"
            )


# ── #7 / N7 · il grafo ─────────────────────────────────────────────────


def test_the_graph_load_can_discover_it_was_superseded() -> None:
    """``loadGraph`` non aveva alcun token: due caricamenti concorrenti
    disegnavano entrambi, l'ultimo a rispondere vinceva a caso."""
    graph = _src(GRAPH_JS)
    body = _method(graph, "loadGraph")
    assert "const token = ++this._loadToken;" in body
    assert "const gen = this._gen;" in body
    assert body.index("await api.getGraph(") < body.index("if (this._stale(token, gen)) return;")
    head = body.partition("pushNav")[0]
    assert "this._stale(token, gen)" in head, "il grafo impila una entry per una schermata mai disegnata"


def test_the_graph_has_exactly_one_load_source() -> None:
    """Una sola pressione di Indietro ne scatenava **due**, di caricamenti.

    ``activate()`` caricava per conto suo, e il chiamante (il ramo ``graph`` del
    popstate, il boot, il pulsante Grafo dell'header) rifaceva ``loadGraph``
    subito dopo ``switchMode``. Due fetch e — molto peggio — due
    ``settleSimulation``, che sono 300 tick d3 sincroni sul main thread.

    La vista voluta ora si *deposita* prima dello switch e la carica
    ``activate()``, che è l'unico punto di caricamento della sezione.
    """
    app = _src(APP_JS)
    graph = _src(GRAPH_JS)
    header = _src(HEADER_JS)

    assert "takePendingGraph" in _method(graph, "activate"), (
        "activate() deve consumare la richiesta invece di indovinare la vista"
    )
    request = _method(app, "requestGraph")
    assert "this._pendingGraph = { wiki: wiki || null, push };" in request
    assert "this.switchMode('graph', false);" in request
    assert "this.takePendingGraph()" in request, (
        "se eravamo già sul grafo nessun activate() consuma la richiesta: va servita qui"
    )

    popstate = re.search(
        r"window\.addEventListener\('popstate'.*?\n    \}\);", _code(app), re.S)
    assert popstate, "listener popstate non trovato"
    assert "this.requestGraph(state.wiki || null, false);" in popstate.group(0)
    assert "loadGraph" not in popstate.group(0), (
        "il popstate carica il grafo da sé: è la seconda sorgente"
    )

    assert "this.controllers.graph.loadGraph(initialWiki, false)" not in app, (
        "il boot ricaricava il grafo subito dopo lo switch che lo aveva già caricato"
    )
    assert app.index("this._pendingGraph = { wiki: initialWiki || null, push: false };") \
        < app.index("this.switchMode(initialMode, false);"), (
        "la richiesta va depositata prima dello switch: activate() è sincrono"
    )

    assert "loadGraph" not in _code(header), (
        "l'header entra nella sezione grafo da requestGraph, non caricando per conto suo"
    )
    assert "app.requestGraph(wiki || null, true);" in header


def test_the_losing_simulation_is_stopped_not_merely_forgotten() -> None:
    """``renderHomeGraph``/``renderWikiGraph`` riassegnano ``this.teardown``
    senza mai invocare quello che c'era: la simulazione d3 perdente non veniva
    fermata da nessuno e continuava a ticchettare su nodi non più a schermo."""
    graph = _src(GRAPH_JS)
    for name in ("renderHomeGraph", "renderWikiGraph"):
        body = _method(graph, name)
        assert "this._cleanup();" in body, f"{name} dimentica la simulazione precedente"
        assert body.index("this._cleanup();") < body.index("this.teardown = () => {"), (
            f"{name}: il cleanup deve precedere la riassegnazione, non seguirla"
        )
    assert "this.teardown();" in _method(graph, "_cleanup")


# ── N4 · il poller di pairing Telegram ─────────────────────────────────


def test_the_telegram_poller_cannot_outlive_its_widget() -> None:
    """Il polling nasceva nella continuazione di ``refresh()``, cioè **dopo**
    che ``deactivate()`` aveva già azzerato il riferimento al widget.

    ``destroy()`` fermava solo il timer già acceso: qui non ce n'era ancora uno.
    Da lì in poi l'oggetto viveva unicamente per la closure dell'intervallo —
    irraggiungibile, quindi non più fermabile — e interrogava il gateway ogni
    2,5 s per sempre, uno in più per ogni giro nelle impostazioni.
    """
    tg = _src(TELEGRAM_JS)
    assert "this._destroyed = false;" in tg
    assert "this._destroyed = true;" in _method(tg, "destroy")

    refresh = _method(tg, "refresh")
    assert refresh.index("await api.getTelegramStatus()") < refresh.index("if (this._destroyed) return;"), (
        "il flag va controllato dopo l'await: prima dell'await non è ancora cambiato niente"
    )
    assert "if (this._destroyed) return;" in _method(tg, "render")
    polling = _method(tg, "_startPolling")
    assert polling.index("if (this._destroyed) return;") < polling.index("setInterval"), (
        "un timer acceso da un widget congedato non lo spegne più nessuno"
    )
    assert "if (this._destroyed) { this._stopPolling(); return; }" in polling, (
        "anche il tick già schedulato deve poter scoprire il congedo"
    )


def test_the_settings_controller_still_destroys_the_widget() -> None:
    """Il flag nel widget si aggiunge al ``_gen`` del controller, non lo
    sostituisce: senza ``destroy()`` il timer di un widget ancora raggiungibile
    resterebbe acceso lo stesso."""
    body = _method(_src(SETTINGS_JS), "deactivate")
    assert "this._tgWidget.destroy();" in body
    assert "this._tgWidget = null;" in body


# ── N18 · Impostazioni carica (e disegna) una volta sola ───────────────


def test_settings_loads_once_not_twice_on_first_open() -> None:
    """Costruttore **e** ``activate()`` caricavano: due GET e due render
    completi alla prima apertura, col secondo che buttava via il DOM del primo
    (widget Telegram e card batteria compresi, ricreati da capo).

    ``this.ready = this.loadSettings()`` non è la soluzione: ``switchMode``
    chiama ``activate()`` comunque, e ``activate()`` ricarica.
    """
    settings = _src(SETTINGS_JS)
    ctor = _method(_code(settings), "constructor")
    assert "loadSettings" not in ctor, "il costruttore carica ancora"
    assert "activate() { this.loadSettings(); }" in settings
    assert "this.ready" not in _code(settings), (
        "un gate `ready` non impedirebbe il secondo caricamento: lo rimanderebbe soltanto"
    )


def test_settings_dom_nodes_are_looked_up_after_the_await() -> None:
    """``render()`` ricostruisce tutto ``contentEl.innerHTML``: un nodo
    catturato prima della fetch è, con ogni probabilità, già staccato dal
    documento. Ci si scriveva dentro senza che a schermo cambiasse niente, e la
    sezione restava sul suo "Caricamento…" per sempre."""
    settings = _src(SETTINGS_JS)
    ssh = _method(settings, "_loadSsh")
    assert ssh.index("await api.getSsh()") < ssh.index(
        "const blockEl = this.contentEl.querySelector('#ssh-block');"
    )
    # L'elenco delle istantanee non vive più dentro `contentEl`: sta nel pannello
    # `drawer-storia`, che è fuori dalla vista (in cassetto resta la riga di
    # riepilogo). Cambia **dove** si cerca, non la regola: il nodo si prende
    # dopo l'await, mai prima — e qui in più il pannello può essersi chiuso nel
    # frattempo, quindi l'assenza del nodo è normale e va gestita, non evitata.
    snapshots = _method(settings, "_loadSnapshotList")
    assert snapshots.index("await api.getSnapshotHistory()") < snapshots.index(
        "const listEl = document.getElementById('snapshot-list');"
    )


# ── N8 · la modale dell'impronta host ──────────────────────────────────


def test_the_ssh_probe_shows_it_is_working_and_never_lands_elsewhere() -> None:
    """La modale dell'impronta si apriva **dopo** un probe di rete lungo, anche
    se l'utente aveva già lasciato Impostazioni: compariva sopra un'altra
    sezione, e "Accetta" scrive davvero in ``known_hosts``.

    La guardia è metà del lavoro. L'altra metà è la causa: durante il probe il
    bottone non si disabilitava e, passati i 3 s del toast, a schermo non
    restava alcun indicatore — è *per questo* che l'utente preme Indietro.
    """
    settings = _src(SETTINGS_JS)
    body = _method(settings, "_sshVerify")
    assert "const gen = this._gen;" in body
    assert body.index("this._setSshVerifyBusy(alias, true);") < body.index(
        "await api.probeSshHostKey(alias)"
    ), "lo stato di attesa deve comparire prima del probe, non dopo"
    assert "} finally {\n      this._setSshVerifyBusy(alias, false);" in body, (
        "il bottone va riabilitato anche quando il probe fallisce"
    )
    busy = _method(settings, "_setSshVerifyBusy")
    assert "btn.disabled = busy;" in busy

    guard = "if (this._stale(gen)) return;"
    assert body.index("await api.probeSshHostKey(alias)") < body.index(guard)
    assert body.index(guard) < body.index("_confirmChangedHostKey"), (
        "la modale si apre solo se siamo ancora nella sezione che l'ha chiesta"
    )
    accept = body.index("await api.acceptSshHostKey(")
    assert guard in body[accept:], "anche la scrittura in known_hosts va ricontrollata"

    assert "if (!accepted) {" in body and "settings.ssh.verifyCancelled" in body, (
        "annullare era l'unica uscita muta della funzione: nessun riscontro, "
        "e il badge 'da verificare' identico a prima"
    )


def test_the_new_string_exists_in_both_locales() -> None:
    for locale in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        assert "verifyCancelled" in data["settings"]["ssh"], locale
