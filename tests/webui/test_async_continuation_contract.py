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

**Sette banchi sono usciti di qui il 21/09/2026**, tutti insieme e per un solo
motivo: guardavano ``mobile-wiki.js`` e ``mobile-graph.js``, e la wiki e' uscita
dall'officina — elenco, mappa e lettore vivono in casa. Con loro se n'e' andato
anche il banco di mermaid, che era il solo lettore di quel vendor. La regola
generale resta e vale per i controller rimasti.

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
    # Erano tre: la wiki e il grafo sono usciti dall'officina il 21/09/2026 e
    # vivono in casa. La regola non cambia — resta quella di **ogni**
    # controller che si sospende su un await — cambia quanti ce ne sono.
    for path in (SETTINGS_JS,):
        source = _src(path)
        assert "this._gen = 0;" in source, f"{path.name}: manca il contatore di generazione"
        assert "this._gen++;" in _method(source, "deactivate"), (
            f"{path.name}: deactivate() non incrementa la generazione"
        )
        assert "_stale(" in source, f"{path.name}: nessuna guardia che legga la generazione"


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
