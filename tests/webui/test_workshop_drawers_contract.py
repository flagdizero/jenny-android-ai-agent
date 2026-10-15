"""I quattro cassetti dell'officina: nessuna sezione persa, nessuna inventata.

Il dock e' passato da cinque voci per sottosistema a quattro per domanda — una
console e tre facolta'. Il meccanismo e' una
tabella sola, `DRAWERS`, e questo file misura le tre cose che quella tabella
puo' sbagliare **in silenzio**:

* **una sezione senza cassetto** sparisce dalla schermata. Non da' errore, non
  lascia un buco: semplicemente non si disegna, e te ne accorgi il giorno in
  cui la cerchi. E' il difetto che questo giro puo' introdurre a ogni passo,
  perche' ogni passo sposta stringhe da un elenco all'altro;
* **un cassetto che nomina una sezione che non esiste** esplode al primo
  tocco (`sections[id]()` su `undefined`), e solo su quel cassetto;
* **la stessa sezione in due cassetti** e' il doppione che il giro esiste per
  togliere, letto dalla parte del codice invece che dal disegno.

E' la stessa forma dell'invariante `BACK_TO` della casa: una tabella che dice
dove si va, e un banco che la incrocia con quel che esiste davvero.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
SETTINGS_JS = ASSETS / "mobile-settings.js"
APP_JS = ASSETS / "mobile-app.js"
WORKSHOP = ROOT / "jenny" / "templates" / "ui" / "workshop.html"


def _src(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _drawers() -> dict[str, dict[str, list[str]]]:
    """La tabella `DRAWERS`, letta dal sorgente."""
    src = _src("mobile-settings.js")
    m = re.search(r"(?ms)^export const DRAWERS = \{(.*?)^\};", src)
    assert m, "CASSETTI non si trova piu': il meccanismo dei cassetti e' sparito"
    body = m.group(1)
    outside = {}
    for name, inside in re.findall(r"(\w+): \{(.*?)\n  \}", body, re.S):
        def list(key: str, open: str = "[", closed: str = "]") -> list[str]:
            entries = re.search(rf"{key}: \{open}([^\{closed}]*)\{closed}", inside)
            return re.findall(r"'([^']+)'", entries.group(1)) if entries else []
        # `doors` non c'e' piu' dal 21/09/2026: era una mappa gruppo -> viste
        # uscite dal dock, e ne e' rimasta una sola — il gestore file, che la
        # scheda «I file veri» si disegna da se'. Un meccanismo generico per
        # una riga era piu' codice della cosa che reggeva. Chi chiede «questa
        # vista si apre da qualche parte?» guarda ora il `data-porta` nel DOM.
        outside[name] = {"sections": list("sections")}
    assert outside, "la tabella e' vuota"
    return outside


def _drawable_sections() -> set[str]:
    """Gli id che `render()` sa costruire."""
    src = _src("mobile-settings.js")
    m = re.search(r"(?s)const sections = \{(.*?)\n    \};", src)
    assert m, "l'elenco delle sezioni dentro render() non si trova piu'"
    return set(re.findall(r"(\w+): \(\) =>", m.group(1)))


def test_every_section_has_exactly_one_drawer() -> None:
    """Il conto nei due versi.

    Una sezione che nessun cassetto nomina non si disegna piu' — e non lo dice
    nessuno. Una che ne nomina due e' il doppione che stiamo togliendo.
    """
    drawable = _drawable_sections()
    collocate: dict[str, list[str]] = {}
    for drawer, content in _drawers().items():
        for section in content["sections"]:
            collocate.setdefault(section, []).append(drawer)

    without_home = sorted(drawable - set(collocate))
    assert not without_home, (
        f"queste sezioni non stanno in nessun cassetto e spariscono dalla "
        f"schermata senza dirlo: {without_home}"
    )
    duplicates = {s: c for s, c in collocate.items() if len(c) > 1}
    assert not duplicates, f"la stessa sezione in piu' cassetti: {duplicates}"


def test_no_drawer_names_a_section_that_does_not_exist() -> None:
    """`sections[id]()` su un id sconosciuto e' un `TypeError` al primo tocco,
    e solo su quel cassetto: gli altri tre restano verdi."""
    drawable = _drawable_sections()
    for drawer, content in _drawers().items():
        ghosts = [s for s in content["sections"] if s not in drawable]
        assert not ghosts, f"il cassetto «{drawer}» nomina sezioni che non esistono: {ghosts}"


def test_every_drawer_in_the_table_is_a_dock_voice_and_the_other_way_round() -> None:
    """Un cassetto nella tabella che il dock non sa aprire e' codice morto; una
    voce del dock senza cassetto disegna la schermata intera — cioe' il difetto
    da cui questo giro parte."""
    table = set(_drawers())
    html = WORKSHOP.read_text(encoding="utf-8")
    nav = html[html.index('<nav class="dock"'):html.index("</nav>")]
    entries = re.findall(r'data-mode="([a-z]+)"', nav)

    # `VIEW_OF` vive accanto a `DRAWERS` (mobile-settings.js): rispondono
    # alla stessa domanda, e tenerle in due file ha gia' prodotto una copia
    # mancante — v. tests/webui/test_workshop_frame_contract.py.
    m = re.search(r"const VIEW_OF = \{([^}]*)\}", _src("mobile-settings.js"))
    assert m, "VIEW_OF non si trova piu'"
    shared = set(re.findall(r"(\w+): 'settings'", m.group(1)))

    assert table == shared, (
        f"la tabella dei cassetti e la mappa delle viste non dicono la stessa "
        f"cosa: {table ^ shared}"
    )
    assert shared <= set(entries), f"cassetti che il dock non apre: {shared - set(entries)}"
    # La console e' l'unica voce che non e' un cassetto: e' la chat.
    assert set(entries) - shared == {"chat"}, set(entries) - shared


def test_the_views_that_left_the_dock_are_still_reachable() -> None:
    """App, file e wiki avevano una voce ciascuna e adesso no. Restano vive:
    senza una porta sarebbero raggiungibili solo da un `switchMode` scritto a
    mano, cioe' da nessuno.

    **La mappa `doors` non esiste piu'.** Delle tre viste ne resta una sola da
    difendere: il gestore file, che si apre da una riga che la scheda «I file
    veri» si disegna da se', in fondo alle cartelle che mostra. Il banco guarda
    percio' le porte **come le vede il DOM** — il `data-porta` nel markup — che
    e' l'unica definizione che conta per chi deve arrivarci col dito.

    Le altre due hanno preso strade diverse, e nessuna delle due passa di qui:
    il cassetto delle app non e' un modo ma un foglio, e la sua maniglia la
    difende `test_launcher_sheet_contract.py`; la wiki e' **uscita
    dall'officina** il 21/09/2026 — elenco, mappa e lettore vivono in casa —
    quindi non c'e' piu' nessun modo `wiki` o `graph` da raggiungere.
    """
    app = _src("mobile-app.js")
    factories = re.search(r"(?s)this\.controllerFactories = \{(.*?)\n    \};", app)
    assert factories, "le fabbriche dei controller non si trovano piu'"
    modes = set(re.findall(r"(\w+):\s", factories.group(1)))

    html = WORKSHOP.read_text(encoding="utf-8")
    nav = html[html.index('<nav class="dock"'):html.index("</nav>")]
    on_the_dock = set(re.findall(r'data-mode="([a-z]+)"', nav))

    settings = _src("mobile-settings.js")
    doors = set(re.findall(r'data-porta="([a-z]+)"', settings))
    # Una vista si raggiunge anche da un gesto scritto nel codice, non solo da
    # una riga su cui si preme. Il file aperto e' cosi' dal 21/09/2026:
    # l'esploratore e' una scheda di Memoria, e ad aprirlo e' il tocco su un
    # file. Senza questa terza fonte il banco chiederebbe di rimettere una porta
    # per una schermata che si raggiunge gia'.
    swipes = set()
    for name in ("mobile-settings.js", "mobile-workspace.js", "mobile-chat.js"):
        swipes |= set(re.findall(r"switchMode\('([a-z]+)'", _src(name)))
    drawers = set(_drawers())
    # `settings` e' il contenitore dei tre cassetti. `onboarding` era esente
    # perche' si apriva da dentro, al primo avvio: dal 27/09/2026 e' un
    # documento suo e non una vista dell'officina. `wiki` era esente perche'
    # «si apre dal grafo»: un anello di due viste che si aprivano a vicenda, e
    # quando la porta e' sparita l'esenzione sarebbe diventata falsa.
    exempt = {"settings"} | drawers
    orphans = sorted(modes - on_the_dock - doors - swipes - exempt)
    assert not orphans, (
        f"queste viste non hanno piu' nessun modo di aprirsi: {orphans}. "
        f"Serve una riga in un cassetto, o un gesto che ci porti."
    )


def test_a_drawer_only_builds_what_it_shows() -> None:
    """Disegnare tutte e undici le sezioni e poi nasconderne otto vorrebbe dire
    costruire ogni volta anche il catalogo dei modelli e la storia degli
    snapshot — su un telefono, a ogni apertura."""
    src = _src("mobile-settings.js")
    m = re.search(r"(?s)this\.contentEl\.innerHTML = \[(.*?)\]\.join\(''\);", src)
    assert m, "il corpo di render() non si trova piu'"
    body = m.group(1)
    assert "which.map((id) => sections[id]()" in body, (
        "render() non disegna piu' per drawer: se le costruisce tutte, le "
        "costruisce tutte anche quando ne mostra una"
    )
    assert "this._renderModelSettings(" not in body, (
        "una sezione viene costruita fuori dalla tabella: torna a pagarsi sempre"
    )


def test_the_three_drawers_share_one_screen_and_one_fetch() -> None:
    """Tre istanze vorrebbero dire tre `/api/settings` e due copie dello stesso
    stato che invecchiano mentre guardi la terza."""
    app = _src("mobile-app.js")
    assert "const settings = () => (this._settings ||= new SettingsController());" in app
    for mode in ("settings", "brain", "hands", "memory"):
        assert re.search(rf"{mode}:\s+settings,", app), f"«{mode}» non condivide il controller"
    # E il cassetto va detto **prima** di activate(), o il primo frame mostra
    # quello di prima.
    i = app.index("next.setDrawer?.(")
    j = app.index("next.activate()")
    assert i < j, "il cassetto viene scelto dopo che la schermata si e' gia' disegnata"


def test_no_drawer_row_leads_out_of_its_drawer_any_more() -> None:
    """Qui stavano due regole sulle righe-porta: una chiave i18n vera (una
    mancante non fallisce — `i18n.t` torna la chiave, e a schermo compare
    «nav.graph», visto sul rig il 20/09/2026) e un'icona propria invece della
    freccia generica.

    Dal 21/09/2026 di righe-porta non ce n'e' piu' nessuna, quindi le due
    regole non hanno soggetto. Al loro posto quel che le rendeva necessarie: un
    cassetto **contiene**, e mandare altrove era l'unica cosa che le porte
    sapessero fare. L'ultima era il gestore file, ed e' finita perche' il
    gestore e' entrato nella scheda.

    La reciproca — che la vista del file resti raggiungibile — la misura
    `test_the_views_that_left_the_dock_are_still_reachable`, che da qui in poi
    conta anche i gesti scritti nel codice.
    """
    src = _src("mobile-settings.js")
    assert "data-porta" not in src, "una riga che porta fuori dal cassetto e' tornata"
    assert "_wirePorte" not in src, "il cablaggio delle porte e' tornato senza porte"


# ── Quel che ha la casa, l'officina non lo rifa' ────────────────────────────

HOME = ASSETS  # gli stessi file: le due interfacce condividono `shared/`


def _home(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def test_the_encrypted_backup_lives_in_one_place() -> None:
    """Esportare e ripristinare da file sono in casa, da «Backup».

    Due schermate che sanno esportare sono due posti da tenere allineati per un
    gesto che si fa una volta al mese — e due posti in cui puo' comparire una
    passphrase. La **storia locale** invece sta in tutti e due: la casa la
    sfoglia e la ripristina sotto la sua riga, perche' chi cerca «il file di
    stamattina» non deve sapere che esiste un'officina; le manopole (crearne
    uno a mano, per quanto tenerla) restano solo di qua.
    """
    workshop = _src("mobile-settings.js")
    for flow in ("runExportFlow", "runImportFlow"):
        assert flow not in workshop, (
            f"l'officina rifa' «{flow}», che la casa ha gia': due posti che "
            f"scrivono lo stesso file"
        )
    for button in ("btn-backup-export", "btn-backup-import"):
        assert button not in workshop, f"{button} e' tornato in officina"

    # E la casa ce li ha davvero: se un giorno sparissero di la', questo banco
    # starebbe difendendo un buco invece di un confine.
    home = _home("home-backup.js")
    assert "runExportFlow" in home and "runImportFlow" in home, (
        "la casa non ha piu' il backup cifrato: toglierlo dall'officina lo "
        "toglierebbe dall'app"
    )

    # La storia locale: il ripristino in tutti e due, le manopole solo qui.
    for piece in ("btn-snapshot-create", "snapshot-retention", "runSnapshotRestore"):
        assert piece in workshop, f"la storia locale ha perso {piece}"
    assert "runSnapshotRestore" in home, "la casa non ripristina piu' gli snapshot"
    for knob in ("createSnapshot", "updateSnapshotRetention"):
        assert knob not in home, f"la casa ha preso anche {knob}, che sta in officina"


def test_choosing_the_model_saves_model_and_brand_together() -> None:
    """Il modello si sceglie in due posti: in casa, da «Chi risponde», e dal
    27/09/2026 in officina, da «Chi pensa», dove ogni marca tiene i suoi
    modelli. Il 20/09 il catalogo era andato tutto in casa; l'officina era
    rimasta con indirizzo e chiave, e dopo una Modifica non c'era modo di
    sapere, da li', se la marca rispondeva.

    Due posti vanno bene finche' fanno la stessa scrittura: un tocco salva
    `model` e `default_provider` **in una chiamata sola**. Due chiamate
    lascerebbero per un attimo un modello di una marca attivo con un'altra
    come provider — una config che non risponde. L'anagrafica invece resta
    **solo** in officina: la casa sostituisce una chiave, non compila un
    endpoint.
    """
    workshop = _src("mobile-settings.js")
    pick = re.search(r"(?s)\n  async _pickBrandModel\(.*?\n  \}\n", workshop)
    assert pick, "l'officina non sceglie piu' il modello da «Chi pensa»"
    assert "api.updateSettings({ model, default_provider: provider })" in pick.group(0), (
        "l'officina non salva piu' modello e marca insieme"
    )
    assert pick.group(0).count("api.updateSettings(") == 1, (
        "modello e marca in due chiamate: fra l'una e l'altra la config non risponde"
    )
    assert "getProviderModels" in workshop, "i gruppi di «Chi pensa» non chiedono l'elenco"

    home = _home("home-model.js")
    assert "getProviderModels" in home, "la casa non chiede piu' l'elenco dei modelli"
    assert "default_provider: provider" in home, (
        "la casa non salva piu' modello e marca insieme: e' il punto del redesign"
    )
    for field in ("dlg-api-base", "dlg-ca-bundle", "dlg-provider-format"):
        assert field in workshop, f"l'anagrafica ha perso {field}"
        assert field not in home, f"la casa ha preso {field}: quello ha bisogno di un paragrafo"


def test_adding_a_brand_finishes_the_job() -> None:
    """Senza il primo modello, aggiungere una marca vorrebbe dire uscire di
    qui, andare in casa e sceglierne uno: **una cosa sola in due posti**, che
    e' il difetto che questo giro esiste per togliere.

    E «usala adesso» e' un interruttore, non un automatismo: attivarla
    d'ufficio cambierebbe chi risponde senza dirlo, con la sorpresa alla
    risposta successiva.
    """
    src = _src("mobile-settings.js")
    for field in ("dlg-first-model", "dlg-use-now"):
        assert field in src, f"il dialogo di aggiunta ha perso {field}"
    assert 'id="dlg-use-now" checked' in src, (
        "«usala adesso» parte spento: nove volte su dieci la aggiungi per usarla"
    )
    assert "firstModel: isEdit || !useNow?.checked" in src and "useItNow: !isEdit" in src, (
        "il primo modello si raccoglie anche in modifica: cambiare l'endpoint di "
        "una marca in uso non deve poter cambiare chi risponde"
    )
    save = re.search(r"(?s)async _saveProvider\(.*?\n  \}", src).group(0)
    i = save.index("api.updateProvider(")
    j = save.index("api.updateSettings(")
    assert i < j, (
        "la marca si attiva prima di esistere: se la prima scrittura fallisce, "
        "`default_provider` punta a un provider che non c'e'"
    )
    assert "useItNow && firstModel" in save, "si attiva anche senza un modello"


def test_the_first_model_goes_with_use_it_now() -> None:
    """Con «Use it now» spento il modello scritto era buttato senza dirlo:
    una marca non ha un modello suo (collaudo del 27/09/2026, decisione del
    29/09: il campo va con l'interruttore). Spento, il campo sparisce e una
    riga dice quando lo si sceglie."""
    src = _src("mobile-settings.js")
    dialog = re.search(r"(?s)_showAddProviderDialog\(existingProvider\) \{.*?\n  \}\n", src).group(0)
    assert 'id="dlg-first-model-field"' in dialog and 'id="dlg-first-model-later" hidden' in dialog
    assert dialog.index('id="dlg-use-now"') < dialog.index('id="dlg-first-model-field"'), (
        "il campo che dipende dall'interruttore sta sopra l'interruttore"
    )
    assert "useNow?.addEventListener('change', syncFirstModel)" in dialog
    assert ".settings-field[hidden] { display: none; }" in _src("mobile-style.css")
    for locale in ("en", "it"):
        data = json.loads((ASSETS / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        assert data["settings"]["firstModelLater"].strip()
        assert "http" in data["settings"]["baseUrlInvalid"]


def test_the_technical_fields_keep_the_keyboard_out() -> None:
    """«http://» diventava «Http:/» con l'autocorrezione, e si salvava così."""
    src = _src("mobile-settings.js")
    dialog = re.search(r"(?s)_showAddProviderDialog\(existingProvider\) \{.*?\n  \}\n", src).group(0)
    for field in ("dlg-provider-name", "dlg-api-key", "dlg-api-base", "dlg-ca-bundle", "dlg-first-model"):
        tag = re.search(rf'<input[^>]*id="{field}"[^>]*>', dialog, re.S).group(0)
        assert "${NO_AUTOCORRECT}" in tag, field
    base = re.search(r'<input[^>]*id="dlg-api-base"[^>]*>', dialog, re.S).group(0)
    assert 'type="url"' in base and 'inputmode="url"' in base
    assert "normalizeApiBase(dialog.querySelector('#dlg-api-base').value)" in dialog
    shared = _src("shared/api-base.js")
    for attr in ('autocorrect="off"', 'autocapitalize="none"', 'spellcheck="false"'):
        assert attr in shared


# ── La wiki e' uscita dall'officina ─────────────────────────────────────────


def test_the_workshop_no_longer_carries_a_wiki_of_its_own() -> None:
    """Due viste, 1.689 righe, e un duplicato di quel che la casa fa gia'.

    L'utente ha tolto la riga «Wiki» da Memoria il 21/09/2026. Quella riga era
    **l'unica entrata**: `wiki` si apriva dal grafo e `graph` dalla wiki — un
    anello di due viste che si aprivano a vicenda — quindi toglierla le avrebbe
    lasciate vive e raggiungibili solo scrivendo l'indirizzo a mano. Sono uscite
    tutte e due.

    Il banco guarda ogni traccia, perche' riportarne indietro una sola non
    ricostruisce la vista ma basta a far ricomparire un bottone che non apre
    niente — che e' il modo in cui questa rimozione puo' andare a meta'.
    """
    for name in ("mobile-wiki.js", "mobile-graph.js"):
        assert not (ASSETS / name).exists(), f"{name} e' tornato"

    for js in sorted(ASSETS.rglob("*.js")):
        if "vendor" in js.parts:
            continue
        for row in js.read_text(encoding="utf-8").splitlines():
            head = row.lstrip()
            assert not (head.startswith("import") and ("mobile-wiki" in row or "mobile-graph" in row)), (
                f"{js.name} importa di nuovo una vista che non c'e'"
            )

    html = WORKSHOP.read_text(encoding="utf-8")
    for node in ('id="view-wiki"', 'id="view-graph"', 'id="drawer-audit"',
                 'id="drawer-files"', 'id="wiki-feedback-dialog"'):
        assert node not in html, f"{node} e' tornato in officina.html"

    from jenny.utils.android_assets import _UI_MANIFEST

    for entry in ("assets/mobile-wiki.js", "assets/mobile-graph.js"):
        assert entry not in _UI_MANIFEST, f"il manifesto elenca ancora {entry}"


def test_the_shell_no_longer_loads_a_library_at_every_boot() -> None:
    """Qui stava la frase piu' sbagliata di tutta la giornata: «Mermaid e KaTeX
    avevano **un solo lettore ciascuno**, ed era la wiki».

    **Falso per KaTeX**, e questo banco non se n'e' accorto perche' chiedeva la
    cosa comoda — che i file fossero spariti — invece di quella vera: che non
    fosse rimasto nessuno a chiamarli. Le formule le disegnava anche la chat
    dell'officina, da quattro punti, e quei quattro punti cominciavano tutti con
    «se la libreria c'e'»: tolta la libreria, hanno smesso di fare qualcosa
    **in silenzio**, e in chat le formule sono tornate `$...$` grezzo.

    La regola giusta — o nessuno la chiama, o la libreria e' spedita — vale per
    tutti i vendor e vive in `test_vendor_contract.py`. Qui resta la parte che
    era davvero dell'officina, ed e' l'altra meta' del difetto originale:
    **niente si carica all'avvio**. KaTeX stava in due `<script defer>` dentro
    `workshop.html`, cioe' 275 kB piu' il CSS a ogni partenza anche solo per
    aprire la chat. Adesso e' pigro come mermaid, e questo banco tiene la porta
    chiusa.
    """
    html = WORKSHOP.read_text(encoding="utf-8")
    for heavy in ("katex", "mermaid", "d3.min.js"):
        assert heavy not in html, (
            f"officina.html carica {heavy} all'avvio: si carica quando serve, "
            f"non a ogni partenza"
        )
    home = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    for heavy in ("katex", "mermaid", "d3.min.js"):
        assert heavy not in home, f"index.html carica {heavy} all'avvio"


def test_the_notebook_did_not_disappear_with_it() -> None:
    """La wiki e' **uscita**, non cancellata: e' la differenza fra spostare una
    stanza e demolirla, e senza questo banco i due sopra sarebbero soddisfatti
    anche da un prodotto che non sa piu' aprire un quaderno.

    La casa ne ha tre pezzi — la bacheca, il cassetto di una scheda e il
    lettore — e le route del server che li nutrono.
    """
    for name in ("home-board.js", "home-board-drawer.js", "home-reader.js"):
        assert (ASSETS / name).exists(), f"{name} manca: il quaderno non si apre da nessuna parte"
    home = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    for node in ('id="home-board"', 'id="home-board-drawer"', 'id="home-reader"'):
        assert node in home, f"{node} manca dalla casa"


@requires_node
def test_the_client_rule_for_the_base_url_is_the_servers() -> None:
    """Il client la dice nella sua lingua, il server la riapplica: due copie
    della stessa regola, e qui si tiene che dicano lo stesso."""
    from jenny.webui.settings_api import WebUISettingsError, normalize_api_base

    cases = ["", "Http://10.0.2.2:8765/v1", "HTTPS://api.example.test/v1", "${BASE}",
             "Http:/10.0.2.2:8765/v1", "ftp://files.example.test", "api.example.test/v1", "https://"]
    expected = []
    for case in cases:
        try:
            expected.append(normalize_api_base(case) or "")
        except WebUISettingsError:
            expected.append(None)
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const {{ normalizeApiBase }} = await import('{(ASSETS / 'shared' / 'api-base.js').as_uri()}');\n"
        f"const cases = {json.dumps(cases)};\n"
        f"const expected = {json.dumps(expected)};\n"
        "const got = cases.map((c) => { const r = normalizeApiBase(c); return r.error ? null : r.value; });\n"
        "assert.deepEqual(got, expected);\n"
    )
