"""I quattro cassetti dell'officina: nessuna sezione persa, nessuna inventata.

Il dock e' passato da cinque voci per sottosistema a quattro per domanda — una
console e tre facolta' (`.agent/officina-tavole-plan.md`). Il meccanismo e' una
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

import re
from pathlib import Path

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
    entries = [m for m in re.findall(r'data-mode="([a-z]+)"', nav) if m != "onboarding"]

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
    # `settings` e' il contenitore dei tre cassetti; `onboarding` si apre da
    # dentro, al primo avvio. `wiki` era esente perche' «si apre dal grafo»:
    # un anello di due viste che si aprivano a vicenda, e quando la porta e'
    # sparita l'esenzione sarebbe diventata falsa. Sono uscite entrambe.
    exempt = {"settings", "onboarding"} | drawers
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
    passphrase. In officina resta la **storia locale**, che e' quel che la casa
    manda a sfogliare qui: «si sfoglia in officina. Vive pero' su questo
    telefono — di un telefono perso non salva niente».
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

    # Quel che resta di qua: la storia locale, con le sue tre manopole.
    for piece in ("btn-snapshot-create", "snapshot-retention", "runSnapshotRestore"):
        assert piece in workshop, f"la storia locale ha perso {piece}"
    assert "runSnapshotRestore" not in home, (
        "la casa ha preso anche gli snapshot: la sua frase manda a sfogliarli qui"
    )


def test_choosing_the_model_lives_in_the_home() -> None:
    """Il catalogo — «Cambia modello», l'elenco per provider, il filtro — e' in
    casa, da «Chi risponde», dove un tocco salva `model` e `default_provider`
    insieme. In officina resta l'anagrafica: formato, endpoint, CA bundle.

    In una riga: in casa scegli fra quel che c'e', in officina decidi cosa
    c'e'. Sono due verbi diversi sullo stesso oggetto — ma un catalogo di qua
    sarebbe la copia, non il secondo verbo.
    """
    workshop = _src("mobile-settings.js")
    for piece in ("model-catalog", "btn-change-model", "_loadModelCatalog", "_selectModel"):
        assert piece not in workshop, f"«{piece}» e' tornato in officina"

    home = _home("home-model.js")
    assert "getProviderModels" in home, "la casa non chiede piu' l'elenco dei modelli"
    assert "default_provider: provider" in home, (
        "la casa non salva piu' modello e marca insieme: e' il punto del redesign"
    )
    # E l'anagrafica resta **solo** di qua: la casa sostituisce una chiave, non
    # compila un endpoint.
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
    assert "firstModel: isEdit ? '' : " in src and "useItNow: !isEdit" in src, (
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

    La casa ne ha tre pezzi — l'elenco, la mappa e il lettore — e le route del
    server che li nutrono non si sono toccate.
    """
    for name in ("home-notebook-pages.js", "home-map.js", "home-reader.js"):
        assert (ASSETS / name).exists(), f"{name} manca: il quaderno non si apre da nessuna parte"
    home = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    for node in ('id="home-notebook-pages"', 'id="home-map"', 'id="home-reader"'):
        assert node in home, f"{node} manca dalla casa"
