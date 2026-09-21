"""I quattro cassetti dell'officina: nessuna sezione persa, nessuna inventata.

Il dock e' passato da cinque voci per sottosistema a quattro per domanda — una
console e tre facolta' (`.agent/officina-tavole-plan.md`). Il meccanismo e' una
tabella sola, `CASSETTI`, e questo file misura le tre cose che quella tabella
puo' sbagliare **in silenzio**:

* **una sezione senza cassetto** sparisce dalla schermata. Non da' errore, non
  lascia un buco: semplicemente non si disegna, e te ne accorgi il giorno in
  cui la cerchi. E' il difetto che questo giro puo' introdurre a ogni passo,
  perche' ogni passo sposta stringhe da un elenco all'altro;
* **un cassetto che nomina una sezione che non esiste** esplode al primo
  tocco (`sezioni[id]()` su `undefined`), e solo su quel cassetto;
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
OFFICINA = ROOT / "jenny" / "templates" / "ui" / "officina.html"


def _src(nome: str) -> str:
    return (ASSETS / nome).read_text(encoding="utf-8")


def _cassetti() -> dict[str, dict[str, list[str]]]:
    """La tabella `CASSETTI`, letta dal sorgente."""
    src = _src("mobile-settings.js")
    m = re.search(r"(?ms)^export const CASSETTI = \{(.*?)^\};", src)
    assert m, "CASSETTI non si trova piu': il meccanismo dei cassetti e' sparito"
    corpo = m.group(1)
    fuori = {}
    for nome, dentro in re.findall(r"(\w+): \{(.*?)\n  \}", corpo, re.S):
        def elenco(chiave: str, aperta: str = "[", chiusa: str = "]") -> list[str]:
            voci = re.search(rf"{chiave}: \{aperta}([^\{chiusa}]*)\{chiusa}", dentro)
            return re.findall(r"'([^']+)'", voci.group(1)) if voci else []
        # `porte` non c'e' piu' dal 21/09/2026: era una mappa gruppo -> viste
        # uscite dal dock, e ne e' rimasta una sola — il gestore file, che la
        # scheda «I file veri» si disegna da se'. Un meccanismo generico per
        # una riga era piu' codice della cosa che reggeva. Chi chiede «questa
        # vista si apre da qualche parte?» guarda ora il `data-porta` nel DOM.
        fuori[nome] = {"sezioni": elenco("sezioni")}
    assert fuori, "la tabella e' vuota"
    return fuori


def _sezioni_disegnabili() -> set[str]:
    """Gli id che `render()` sa costruire."""
    src = _src("mobile-settings.js")
    m = re.search(r"(?s)const sezioni = \{(.*?)\n    \};", src)
    assert m, "l'elenco delle sezioni dentro render() non si trova piu'"
    return set(re.findall(r"(\w+): \(\) =>", m.group(1)))


def test_every_section_has_exactly_one_drawer() -> None:
    """Il conto nei due versi.

    Una sezione che nessun cassetto nomina non si disegna piu' — e non lo dice
    nessuno. Una che ne nomina due e' il doppione che stiamo togliendo.
    """
    disegnabili = _sezioni_disegnabili()
    collocate: dict[str, list[str]] = {}
    for cassetto, contenuto in _cassetti().items():
        for sezione in contenuto["sezioni"]:
            collocate.setdefault(sezione, []).append(cassetto)

    senza_casa = sorted(disegnabili - set(collocate))
    assert not senza_casa, (
        f"queste sezioni non stanno in nessun cassetto e spariscono dalla "
        f"schermata senza dirlo: {senza_casa}"
    )
    doppie = {s: c for s, c in collocate.items() if len(c) > 1}
    assert not doppie, f"la stessa sezione in piu' cassetti: {doppie}"


def test_no_drawer_names_a_section_that_does_not_exist() -> None:
    """`sezioni[id]()` su un id sconosciuto e' un `TypeError` al primo tocco,
    e solo su quel cassetto: gli altri tre restano verdi."""
    disegnabili = _sezioni_disegnabili()
    for cassetto, contenuto in _cassetti().items():
        fantasmi = [s for s in contenuto["sezioni"] if s not in disegnabili]
        assert not fantasmi, f"il cassetto «{cassetto}» nomina sezioni che non esistono: {fantasmi}"


def test_every_drawer_in_the_table_is_a_dock_voice_and_the_other_way_round() -> None:
    """Un cassetto nella tabella che il dock non sa aprire e' codice morto; una
    voce del dock senza cassetto disegna la schermata intera — cioe' il difetto
    da cui questo giro parte."""
    tabella = set(_cassetti())
    html = OFFICINA.read_text(encoding="utf-8")
    nav = html[html.index('<nav class="dock"'):html.index("</nav>")]
    voci = [m for m in re.findall(r'data-mode="([a-z]+)"', nav) if m != "onboarding"]

    # `VISTA_DI` vive accanto a `CASSETTI` (mobile-settings.js): rispondono
    # alla stessa domanda, e tenerle in due file ha gia' prodotto una copia
    # mancante — v. tests/webui/test_officina_cornice_contract.py.
    m = re.search(r"const VISTA_DI = \{([^}]*)\}", _src("mobile-settings.js"))
    assert m, "VISTA_DI non si trova piu'"
    condivisi = set(re.findall(r"(\w+): 'settings'", m.group(1)))

    assert tabella == condivisi, (
        f"la tabella dei cassetti e la mappa delle viste non dicono la stessa "
        f"cosa: {tabella ^ condivisi}"
    )
    assert condivisi <= set(voci), f"cassetti che il dock non apre: {condivisi - set(voci)}"
    # La console e' l'unica voce che non e' un cassetto: e' la chat.
    assert set(voci) - condivisi == {"chat"}, set(voci) - condivisi


def test_the_views_that_left_the_dock_are_still_reachable() -> None:
    """App, file e wiki avevano una voce ciascuna e adesso no. Restano vive:
    senza una porta sarebbero raggiungibili solo da un `switchMode` scritto a
    mano, cioe' da nessuno.

    **La mappa `porte` non esiste piu'.** Delle tre viste ne resta una sola da
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
    fabbriche = re.search(r"(?s)this\.controllerFactories = \{(.*?)\n    \};", app)
    assert fabbriche, "le fabbriche dei controller non si trovano piu'"
    modi = set(re.findall(r"(\w+):\s", fabbriche.group(1)))

    html = OFFICINA.read_text(encoding="utf-8")
    nav = html[html.index('<nav class="dock"'):html.index("</nav>")]
    sul_dock = set(re.findall(r'data-mode="([a-z]+)"', nav))

    impostazioni = _src("mobile-settings.js")
    porte = set(re.findall(r'data-porta="([a-z]+)"', impostazioni))
    cassetti = set(_cassetti())
    # `settings` e' il contenitore dei tre cassetti; `onboarding` si apre da
    # dentro, al primo avvio. `wiki` era esente perche' «si apre dal grafo»:
    # un anello di due viste che si aprivano a vicenda, e quando la porta e'
    # sparita l'esenzione sarebbe diventata falsa. Sono uscite entrambe.
    esenti = {"settings", "onboarding"} | cassetti
    orfani = sorted(modi - sul_dock - porte - esenti)
    assert not orfani, (
        f"queste viste non hanno piu' nessun modo di aprirsi: {orfani}. "
        f"Vanno messe fra le `porte` di un cassetto finche' non hanno la loro riga."
    )


def test_a_drawer_only_builds_what_it_shows() -> None:
    """Disegnare tutte e undici le sezioni e poi nasconderne otto vorrebbe dire
    costruire ogni volta anche il catalogo dei modelli e la storia degli
    snapshot — su un telefono, a ogni apertura."""
    src = _src("mobile-settings.js")
    m = re.search(r"(?s)this\.contentEl\.innerHTML = \[(.*?)\]\.join\(''\);", src)
    assert m, "il corpo di render() non si trova piu'"
    corpo = m.group(1)
    assert "quali.map((id) => sezioni[id]()" in corpo, (
        "render() non disegna piu' per cassetto: se le costruisce tutte, le "
        "costruisce tutte anche quando ne mostra una"
    )
    assert "this._renderModelSettings(" not in corpo, (
        "una sezione viene costruita fuori dalla tabella: torna a pagarsi sempre"
    )


def test_the_three_drawers_share_one_screen_and_one_fetch() -> None:
    """Tre istanze vorrebbero dire tre `/api/settings` e due copie dello stesso
    stato che invecchiano mentre guardi la terza."""
    app = _src("mobile-app.js")
    assert "const impostazioni = () => (this._impostazioni ||= new SettingsController());" in app
    for modo in ("settings", "cervello", "mani", "memoria"):
        assert re.search(rf"{modo}:\s+impostazioni,", app), f"«{modo}» non condivide il controller"
    # E il cassetto va detto **prima** di activate(), o il primo frame mostra
    # quello di prima.
    i = app.index("next.setCassetto?.(")
    j = app.index("next.activate()")
    assert i < j, "il cassetto viene scelto dopo che la schermata si e' gia' disegnata"


def test_the_one_door_left_says_its_name_and_wears_its_own_icon() -> None:
    """Due banchi, uniti quando la mappa delle porte e' sparita.

    Il primo: una chiave i18n che non esiste non fallisce — `i18n.t` torna la
    chiave, e a schermo compare «nav.graph». Visto sul rig il 20/09/2026.
    Il secondo: il ripiego dell'icona era una freccia generica, e due porte con
    la stessa freccia si distinguono solo leggendo, che e' quel che un'icona
    serve a evitare.

    Oggi la porta e' una sola e sta nel markup della scheda «I file veri», non
    in una tabella: percio' le due regole si misurano li'. L'icona non e' piu'
    un ripiego ma una scelta scritta a mano, e la chiave dev'essere una vera.
    """
    import json

    src = _src("mobile-settings.js")
    righe = re.findall(
        r'<button class="settings-porta" data-porta="(\w+)"[^>]*>(.*?)</button>', src, re.S
    )
    assert righe, "nessuna riga-porta nel sorgente: il gestore file e' irraggiungibile"
    for porta, corpo in righe:
        chiave = re.search(r"i18n\.t\('nav\.(\w+)'\)", corpo)
        assert chiave, f"la porta «{porta}» non prende il nome da una chiave `nav.*`"
        for locale in ("it", "en"):
            voci = json.loads(
                (ASSETS / "i18n" / f"{locale}.json").read_text(encoding="utf-8")
            )["nav"]
            assert voci.get(chiave.group(1), "").strip(), (
                f"la porta «{porta}» non ha un nome in {locale}.json: a schermo "
                f"comparirebbe «nav.{chiave.group(1)}»"
            )
        icone = re.findall(r'class="ti ti-([\w-]+)"', corpo)
        assert icone and icone[0] != "arrow-right", (
            f"la porta «{porta}» porta la freccia generica invece di un'icona sua"
        )


# ── Quel che ha la casa, l'officina non lo rifa' ────────────────────────────

CASA = ASSETS  # gli stessi file: le due interfacce condividono `shared/`


def _casa(nome: str) -> str:
    return (ASSETS / nome).read_text(encoding="utf-8")


def test_the_encrypted_backup_lives_in_one_place() -> None:
    """Esportare e ripristinare da file sono in casa, da «Backup».

    Due schermate che sanno esportare sono due posti da tenere allineati per un
    gesto che si fa una volta al mese — e due posti in cui puo' comparire una
    passphrase. In officina resta la **storia locale**, che e' quel che la casa
    manda a sfogliare qui: «si sfoglia in officina. Vive pero' su questo
    telefono — di un telefono perso non salva niente».
    """
    officina = _src("mobile-settings.js")
    for gesto in ("runExportFlow", "runImportFlow"):
        assert gesto not in officina, (
            f"l'officina rifa' «{gesto}», che la casa ha gia': due posti che "
            f"scrivono lo stesso file"
        )
    for bottone in ("btn-backup-export", "btn-backup-import"):
        assert bottone not in officina, f"{bottone} e' tornato in officina"

    # E la casa ce li ha davvero: se un giorno sparissero di la', questo banco
    # starebbe difendendo un buco invece di un confine.
    casa = _casa("casa-backup.js")
    assert "runExportFlow" in casa and "runImportFlow" in casa, (
        "la casa non ha piu' il backup cifrato: toglierlo dall'officina lo "
        "toglierebbe dall'app"
    )

    # Quel che resta di qua: la storia locale, con le sue tre manopole.
    for pezzo in ("btn-snapshot-create", "snapshot-retention", "runSnapshotRestore"):
        assert pezzo in officina, f"la storia locale ha perso {pezzo}"
    assert "runSnapshotRestore" not in casa, (
        "la casa ha preso anche gli snapshot: la sua frase manda a sfogliarli qui"
    )


def test_choosing_the_model_lives_in_the_casa() -> None:
    """Il catalogo — «Cambia modello», l'elenco per provider, il filtro — e' in
    casa, da «Chi risponde», dove un tocco salva `model` e `default_provider`
    insieme. In officina resta l'anagrafica: formato, endpoint, CA bundle.

    In una riga: in casa scegli fra quel che c'e', in officina decidi cosa
    c'e'. Sono due verbi diversi sullo stesso oggetto — ma un catalogo di qua
    sarebbe la copia, non il secondo verbo.
    """
    officina = _src("mobile-settings.js")
    for pezzo in ("model-catalog", "btn-change-model", "_loadModelCatalog", "_selectModel"):
        assert pezzo not in officina, f"«{pezzo}» e' tornato in officina"

    casa = _casa("casa-model.js")
    assert "getProviderModels" in casa, "la casa non chiede piu' l'elenco dei modelli"
    assert "default_provider: provider" in casa, (
        "la casa non salva piu' modello e marca insieme: e' il punto del redesign"
    )
    # E l'anagrafica resta **solo** di qua: la casa sostituisce una chiave, non
    # compila un endpoint.
    for campo in ("dlg-api-base", "dlg-ca-bundle", "dlg-provider-format"):
        assert campo in officina, f"l'anagrafica ha perso {campo}"
        assert campo not in casa, f"la casa ha preso {campo}: quello ha bisogno di un paragrafo"


def test_adding_a_brand_finishes_the_job() -> None:
    """Senza il primo modello, aggiungere una marca vorrebbe dire uscire di
    qui, andare in casa e sceglierne uno: **una cosa sola in due posti**, che
    e' il difetto che questo giro esiste per togliere.

    E «usala adesso» e' un interruttore, non un automatismo: attivarla
    d'ufficio cambierebbe chi risponde senza dirlo, con la sorpresa alla
    risposta successiva.
    """
    src = _src("mobile-settings.js")
    for campo in ("dlg-first-model", "dlg-use-now"):
        assert campo in src, f"il dialogo di aggiunta ha perso {campo}"
    assert 'id="dlg-use-now" checked' in src, (
        "«usala adesso» parte spento: nove volte su dieci la aggiungi per usarla"
    )
    assert "primoModello: isEdit ? '' : " in src and "usalaAdesso: !isEdit" in src, (
        "il primo modello si raccoglie anche in modifica: cambiare l'endpoint di "
        "una marca in uso non deve poter cambiare chi risponde"
    )
    salva = re.search(r"(?s)async _saveProvider\(.*?\n  \}", src).group(0)
    i = salva.index("api.updateProvider(")
    j = salva.index("api.updateSettings(")
    assert i < j, (
        "la marca si attiva prima di esistere: se la prima scrittura fallisce, "
        "`default_provider` punta a un provider che non c'e'"
    )
    assert "usalaAdesso && primoModello" in salva, "si attiva anche senza un modello"


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
    for nome in ("mobile-wiki.js", "mobile-graph.js"):
        assert not (ASSETS / nome).exists(), f"{nome} e' tornato"

    for js in sorted(ASSETS.rglob("*.js")):
        if "vendor" in js.parts:
            continue
        for riga in js.read_text(encoding="utf-8").splitlines():
            testa = riga.lstrip()
            assert not (testa.startswith("import") and ("mobile-wiki" in riga or "mobile-graph" in riga)), (
                f"{js.name} importa di nuovo una vista che non c'e'"
            )

    html = OFFICINA.read_text(encoding="utf-8")
    for nodo in ('id="view-wiki"', 'id="view-graph"', 'id="drawer-audit"',
                 'id="drawer-files"', 'id="wiki-feedback-dialog"'):
        assert nodo not in html, f"{nodo} e' tornato in officina.html"

    from jenny.utils.android_assets import _UI_MANIFEST

    for voce in ("assets/mobile-wiki.js", "assets/mobile-graph.js"):
        assert voce not in _UI_MANIFEST, f"il manifesto elenca ancora {voce}"


def test_the_two_bundles_the_wiki_carried_are_gone_with_it() -> None:
    """Mermaid (3,2 MB) e KaTeX (1,4 MB) avevano **un solo lettore ciascuno**,
    ed era la wiki dell'officina: i diagrammi di una nota e il LaTeX a
    richiesta. Nessuno dei due e' mai stato caricato dalla casa.

    Restano nel prodotto solo se qualcuno li carica: un vendor spedito e mai
    eseguito e' peso nell'APK e una licenza da tenere aggiornata per niente.
    Percio' si misurano tre cose insieme — il codice, il manifesto e le note di
    licenza — perche' e' esattamente la terna che l'altra volta si era
    disallineata (v. `test_third_party_licenses_are_actually_shipped`).
    """
    from jenny.utils.android_assets import _UI_MANIFEST

    for libreria in ("mermaid", "katex"):
        assert not list((ASSETS / "vendor").glob(f"{libreria}*")), (
            f"{libreria} e' tornato su disco senza nessuno che lo carichi"
        )
        assert not [v for v in _UI_MANIFEST if libreria in v], (
            f"il manifesto spedisce ancora {libreria}"
        )
        note = (ASSETS.parents[3] / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        assert libreria not in note.lower(), (
            f"le note promettono ancora {libreria}, che non e' piu' nel bundle"
        )

    html = OFFICINA.read_text(encoding="utf-8")
    for morto in ("katex", "mermaid", "d3.min.js"):
        assert morto not in html, f"officina.html carica ancora {morto}"


def test_the_notebook_did_not_disappear_with_it() -> None:
    """La wiki e' **uscita**, non cancellata: e' la differenza fra spostare una
    stanza e demolirla, e senza questo banco i due sopra sarebbero soddisfatti
    anche da un prodotto che non sa piu' aprire un quaderno.

    La casa ne ha tre pezzi — l'elenco, la mappa e il lettore — e le route del
    server che li nutrono non si sono toccate.
    """
    for nome in ("casa-pages.js", "casa-map.js", "casa-reader.js"):
        assert (ASSETS / nome).exists(), f"{nome} manca: il quaderno non si apre da nessuna parte"
    casa = (ASSETS.parent / "index.html").read_text(encoding="utf-8")
    for nodo in ('id="casa-pages"', 'id="casa-map"', 'id="casa-reader"'):
        assert nodo in casa, f"{nodo} manca dalla casa"
