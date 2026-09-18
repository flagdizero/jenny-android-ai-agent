"""Le invarianti delle **tre stanze** della scheda Apps.

La fisarmonica di prima — tre sezioni in un unico scorrimento — è stata
sostituita da un segmento in cima e una stanza per volta. Qui si tengono ferme
le proprietà per cui quel cambio è stato fatto, e che si perderebbero senza
rumore perché nessuna di loro rompe qualcosa quando sparisce:

* **una stanza per volta** — il contenuto è una scrittura sola, e le altre due
  stanze non stanno nel DOM a costare scorrimento;
* **difetto 05** — l'errore di una Jenny App rotta è una banda *in cima alla
  stanza*, non un blocco dentro la sua riga: lì alzava la cella da 100 a 147 px
  e con lei tutta la fila;
* **difetto 03** — nessun glifo per riga nella stanza Skill: dodici icone
  puzzle identiche non distinguevano niente;
* **difetto 07** — la stanza attiva si ricorda fra una visita e l'altra, che è
  ciò che le sezioni chiuse dell'accordion non facevano;
* **lo stato di una skill non è binario**: `disabled` è una decisione (lo dice
  l'interruttore), `available === false` è un impedimento (lo dice una riga in
  `var(--warning)` con dentro `unavailable_reason`). Prima erano lo stesso
  badge, e i due casi si somigliavano solo a guardarli;
* **le skill di serie si vedono**, col lucchetto invece dell'interruttore — e
  senza azioni di gestione, che il gateway rifiuterebbe comunque.

Asserzioni sul sorgente, nello stile del resto di ``tests/webui/``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
APPS_JS = ASSETS / "mobile-apps.js"
CSS = ASSETS / "mobile-style.css"
INDEX = ROOT / "jenny" / "templates" / "ui" / "officina.html"


def _src(name: str = "mobile-apps.js") -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


def _rule(css: str, selector: str) -> str:
    match = re.search(rf"\n{re.escape(selector)} \{{(.*?)\n\}}", css, re.S)
    assert match, f"regola {selector} non trovata"
    return match.group(1)


# ── una stanza per volta ────────────────────────────────────────────────────

def test_there_are_three_rooms_and_the_segment_names_them_all() -> None:
    source = _src()
    rooms = re.search(r"const ROOMS = \[(.*?)\];", source, re.S)
    assert rooms, "ROOMS non trovato"
    assert [r.strip().strip("'") for r in rooms.group(1).split(",") if r.strip()] == [
        "jenny",
        "skills",
        "android",
    ]
    tabs = _method(source, "_renderRoomTabs")
    assert "ROOMS.map" in tabs, "il segmento si costruisce dalla lista, non a mano"
    assert "aria-selected" in tabs, "quale stanza è attiva va detto, non solo colorato"
    assert INDEX.read_text(encoding="utf-8").count('id="apps-rooms"') == 1


def test_only_the_active_room_is_in_the_dom() -> None:
    """Il punto di tutto il lavoro: 5,9 schermate di scorrimento erano tre
    stanze impilate. Se il contenuto tornasse a essere la somma delle tre, la
    scheda tornerebbe quella di prima con un segmento decorativo sopra."""
    body = _method(_src(), "render")
    assert body.count("this.contentEl.replaceChildren(") == 1, (
        "una sola scrittura, e con dentro una sola stanza"
    )
    assert "_renderSkillsRoom" in body and "_renderAndroidRoom" in body \
        and "_renderJennyRoom" in body
    assert "this.activeRoom === 'skills'" in body and "this.activeRoom === 'android'" in body


def test_the_active_room_survives_leaving_the_view() -> None:
    """Difetto 07: le sezioni chiuse dell'accordion si riaprivano tutte a ogni
    rientro. `localStorage` può alzare eccezioni (finestra privata, dati del
    sito bloccati) e una scheda che non si apre per quello sarebbe un guasto
    sproporzionato: entrambi gli accessi sono protetti."""
    source = _src()
    assert "const ROOM_STORAGE_KEY = 'apps-active-room';" in source
    load = _method(source, "_loadActiveRoom")
    assert "localStorage.getItem(ROOM_STORAGE_KEY)" in load
    assert "ROOMS.includes(saved)" in load, "una chiave stantia non deve scegliere la stanza"
    assert "catch" in load
    set_room = _method(source, "setRoom")
    assert "localStorage.setItem(ROOM_STORAGE_KEY, room)" in set_room
    assert "catch" in set_room


def test_the_search_field_filters_the_active_room_only() -> None:
    """Il campo resta uno — 47 app Android senza ricerca sono scomode — ma non
    filtra più tutte e tre le liste insieme: la query si azzera cambiando
    stanza, o mostrerebbe una stanza vuota senza una ragione visibile."""
    source = _src()
    set_room = _method(source, "setRoom")
    assert "this.searchInput.value = ''" in set_room
    render = _method(source, "render")
    assert "this.searchInput?.value" in render
    # Ogni stanza riceve la query e ci filtra la propria lista.
    for room in ("_renderJennyRoom", "_renderSkillsRoom", "_renderAndroidRoom"):
        assert "this._matches(q" in _method(source, room), f"{room} non filtra sulla query"


# ── difetto 05 — l'errore è una banda, non un blocco nella riga ─────────────

def test_a_broken_app_puts_its_error_in_a_band_above_the_room() -> None:
    source = _src()
    room = _method(source, "_renderJennyRoom")
    assert "_buildBrokenBand" in room
    # La banda sta **prima** della lista: sotto sarebbe una nota a piè di pagina.
    assert room.index("_buildBrokenBand") < room.index("_buildJennyRow")
    band = _method(source, "_buildBrokenBand")
    assert "apps-band--error" in band
    assert "textContent" in band and "innerHTML" not in band


def test_the_broken_row_says_repair_and_carries_no_error_text() -> None:
    """Difetto 05: l'errore dentro la tessera portava quella cella da 100 a
    147 px. La riga dice cosa fare («ripara»); il perché sta nella banda."""
    row = _method(_src(), "_buildJennyRow")
    assert "apps.repair" in row
    assert "app.error" not in row, (
        "l'errore non torna dentro la riga: è la banda a portarlo (difetto 05)"
    )
    css = CSS.read_text(encoding="utf-8")
    for selector in (".apps-row-desc", ".apps-row-warn"):
        assert "-webkit-line-clamp: 2" in _rule(css, selector), (
            f"{selector}: senza un tetto di righe un testo lungo alza la riga e "
            "rompe il passo della lista (difetto 04)"
        )


# ── difetto 03 — nessuna icona per riga nella stanza Skill ──────────────────

def test_no_puzzle_glyph_per_skill_row() -> None:
    """Dodici glifi puzzle identici non distinguevano niente e costavano il 22%
    della cella. La riga di una skill non ha icona: ha un nome."""
    row = _method(_src(), "_buildSkillRow")
    assert "ti-puzzle" not in row
    assert "createElement('i')" not in row


# ── lo stato di una skill non è binario ─────────────────────────────────────

def test_disabled_and_unavailable_are_two_different_things() -> None:
    source = _src()
    row = _method(source, "_buildSkillRow")
    assert "skill.available === false" in row, (
        "«non può girare» va letto da `available`, non dedotto da `disabled`"
    )
    assert "skill.unavailable_reason" in row, (
        "senza il motivo, «non disponibile» non dice cosa fare"
    )
    assert "apps-row-warn" in row
    toggle = _method(source, "_buildSkillToggle")
    assert "input.checked = !skill.disabled" in toggle, (
        "l'interruttore dice `disabled` — la decisione dell'utente — e nient'altro"
    )
    warn = _rule(CSS.read_text(encoding="utf-8"), ".apps-row-warn")
    assert "var(--warning)" in warn, "token esistente, niente colori cablati"


def test_the_toggle_reuses_the_settings_switch() -> None:
    """Un secondo interruttore con un aspetto suo sarebbe una seconda cosa da
    imparare per la stessa azione."""
    toggle = _method(_src(), "_buildSkillToggle")
    assert "'toggle-switch apps-row-toggle'" in toggle
    assert "'toggle-slider'" in toggle
    css = CSS.read_text(encoding="utf-8")
    assert ".toggle-switch {" in css and ".toggle-slider {" in css, (
        "le due classi devono restare quelle delle Impostazioni, non copie"
    )


def test_a_failed_toggle_goes_back_where_it_was() -> None:
    """In caso di successo `loadSkills()` ridisegna e il nodo sparisce; in caso
    di errore il ridisegno non avviene, e una casella lasciata dove l'ha messa
    il dito direbbe che la skill è spenta mentre il gateway la tiene accesa."""
    body = _method(_src(), "_onSkillToggled")
    assert "this._setSkillDisabled(name, disabled)" in body
    assert "await this.loadSkills()" in body
    assert "input.checked = !disabled" in body


# ── le skill di serie si vedono, ma non si spengono ─────────────────────────

def test_built_in_skills_are_no_longer_filtered_away() -> None:
    """`loadSkills` filtrava `source === 'workspace'`: l'agente usava le skill
    di serie, la scheda non le nominava, e la sola traccia della loro esistenza
    era una risposta che arrivava da un pezzo di macchina invisibile."""
    load = _method(_src(), "loadSkills")
    assert "filter(s => s.source === 'workspace')" not in load
    assert "data.skills || []" in load


def test_built_in_is_read_from_the_frontmatter_not_from_source() -> None:
    """Misurato sul telefono, e il piano diceva un'altra cosa: le skill
    impacchettate vengono **copiate** in ``workspace/skills/`` al boot e
    ``SkillsLoader.list_skills`` guarda solo lì, quindi ``source`` vale
    ``"workspace"`` per tutte. Un gruppo "Di serie" costruito su quel campo
    resterebbe vuoto per sempre, e nessun test lo direbbe."""
    built_in = _method(_src(), "_skillIsBuiltIn")
    assert "skill.locked" in built_in and "skill.internal" in built_in
    assert "source" not in built_in
    loader = (ROOT / "jenny" / "agent" / "skills.py").read_text(encoding="utf-8")
    list_skills = re.search(r"def list_skills\(.*?\n    def ", loader, re.S)
    assert list_skills, "list_skills non trovata"
    assert '"workspace"' in list_skills.group(0)
    assert list_skills.group(0).count("_skill_entries_from_dir") == 1, (
        "se un giorno il loader elencasse una seconda sorgente, il criterio "
        "di `_skillIsBuiltIn` andrebbe riaperto: è questa riga a saperlo"
    )


def test_a_locked_skill_keeps_its_lock_and_gets_no_switch() -> None:
    """Fuori dalla Modalità avanzata la scheda di `cron` o di `ssh` non ha mai
    offerto "Disabilita". Un interruttore su quelle righe vorrebbe dire poter
    spegnere il cron con un tocco: una protezione che c'era, tolta per
    distrazione."""
    source = _src()
    manageable = _method(source, "_skillIsManageable")
    assert "!skill.locked || advancedMode()" in manageable
    assert "skill.source === 'workspace'" in manageable
    row = _method(source, "_buildSkillRow")
    assert "this._skillIsManageable(skill)" in row
    assert "_buildSkillToggle" in row and "_buildSkillLock" in row
    lock = _method(source, "_buildSkillLock")
    assert "ti-lock" in lock
    assert "aria-label" in lock, "un'icona sola non si annuncia da sé"
    # Una regola sola: la stessa che decide le azioni della scheda. Due copie
    # divergerebbero, e una delle due offrirebbe ciò che l'altra protegge.
    assert "!this._skillIsManageable(skill)" in _method(source, "showSkillSheet")
    assert "this._skillIsManageable(skill)" in _method(source, "_openSkill")


def test_the_group_says_where_a_skill_comes_from_not_what_it_allows() -> None:
    """Accendere la Modalità avanzata non deve far saltare una riga da un
    gruppo all'altro: cambia il suo comando (lucchetto → interruttore), non la
    sua provenienza."""
    room = _method(_src(), "_renderSkillsRoom")
    assert "!this._skillIsBuiltIn(skill)" in room
    assert "this._skillIsBuiltIn(skill)" in room
    assert "_skillIsManageable" not in room


def test_internal_skills_still_need_advanced_mode() -> None:
    """Cambio di perimetro voluto (le skill di serie), non voluto (le
    `internal`): quelle restano nascoste fuori dalla Modalità avanzata."""
    room = _method(_src(), "_renderSkillsRoom")
    assert "advancedMode() || !skill.internal" in room


# ── la stanza Android ───────────────────────────────────────────────────────

def test_the_android_grid_is_six_columns_with_an_edge_guide() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert "grid-template-columns: repeat(6, 1fr)" in _rule(css, ".apps-grid"), (
        "sei colonne: a quattro, ogni cella sprecava 56 px di larghezza"
    )
    source = _src()
    room = _method(source, "_renderAndroidRoom")
    assert "_buildAzRail" in room
    assert "anchors.size > 1" in room, (
        "una guida con una lettera sola non guida da nessuna parte"
    )
    rail = _method(source, "_buildAzRail")
    assert "scrollIntoView" in rail
    assert "aria-label" in rail


def test_the_guide_appears_only_when_there_is_something_to_scroll() -> None:
    """Misurato sul telefono: con 18 app la griglia a sei colonne sta in tre
    file e non scorre affatto. Una guida lì sarebbe una colonna di lettere che
    non porta da nessuna parte, e si prenderebbe 22 px di larghezza alla
    griglia per sempre. Si misura, non si indovina da un conteggio di righe:
    l'altezza disponibile cambia con la tastiera e con lo schermo."""
    source = _src()
    sync = _method(source, "_syncAzRail")
    assert "scroll.scrollHeight <= scroll.clientHeight" in sync
    assert "rail.isConnected" in sync, (
        "prima di stare in pagina scrollHeight e clientHeight valgono zero"
    )
    render = _method(source, "render")
    assert render.index("replaceChildren") < render.index("_syncAzRail()"), (
        "si misura dopo l'inserimento, o la guida sparirebbe sempre"
    )
    # E si azzera **prima** di costruire la stanza: è `_renderAndroidRoom` a
    # riempire `_azRail`, e pulirlo dopo cancella proprio ciò che ha scritto —
    # la guida resta allora sempre a schermo. Difetto visto sul telefono, non
    # ipotetico: le due righe erano invertite.
    assert render.index("this._azRail = null") < render.index("this._renderAndroidRoom(q)")
    # `display: flex` sulla classe batte la regola dello user agent per
    # `[hidden]`: senza questa riga la guida resta a schermo (visto sul telefono).
    assert ".apps-az[hidden] { display: none; }" in CSS.read_text(encoding="utf-8")


def test_the_index_letter_folds_accents_and_buckets_the_rest() -> None:
    body = _method(_src(), "_indexLetter")
    assert "normalize('NFD')" in body, "«Élite» sotto E, non in un secchio da una voce"
    assert "'#'" in body, "cifre ed emoji hanno bisogno di un secchio"


def test_the_room_owns_its_scrolling_so_the_guide_can_stand_still() -> None:
    css = CSS.read_text(encoding="utf-8")
    content = _rule(css, ".apps-content")
    assert "overflow: hidden" in content, (
        "se a scorrere è il contenitore, la guida A–Z scorre via col resto"
    )
    assert "overflow-y: auto" in _rule(css, ".apps-room-scroll")


def test_hidden_apps_still_wait_for_both_lists() -> None:
    """Sulla corsa del primo caricamento una app nascosta comparirebbe per un
    istante. Stessa guardia di `launcherEntries()`."""
    room = _method(_src(), "_renderAndroidRoom")
    assert "this._androidAppsLoaded && this._hiddenLoaded" in room
    assert "this._showHidden || !this.hiddenPackages.has(app.packageName)" in room


def test_the_eye_shows_up_in_the_android_room_only() -> None:
    source = _src()
    sync = _method(source, "_syncHeaderActions")
    assert "this.activeRoom === 'android'" in sync
    assert "showAction('toggle-hidden')" in sync and "hideAction('toggle-hidden')" in sync
    assert "this._syncHeaderActions()" in _method(source, "render"), (
        "la visibilità dell'occhio va rifatta a ogni disegno: `header.setMode` "
        "lo ridisegna acceso a ogni ingresso nella scheda"
    )
    # Uscendo dalla stanza si spegne anche lo stato, o l'icona direbbe una cosa
    # e la griglia un'altra al rientro.
    assert "this._showHidden = false" in _method(source, "setRoom")


# ── il testo che arriva da fuori ────────────────────────────────────────────

def test_no_room_builder_ever_writes_html() -> None:
    """Nomi, descrizioni ed errori arrivano dal PackageManager e da manifest
    scritti da un LLM: si scrivono con `textContent`, mai concatenati."""
    source = _src()
    builders = (
        "_rowMain",
        "_buildAddRow",
        "_buildJennyRow",
        "_buildBrokenBand",
        "_buildSkillRow",
        "_buildSkillToggle",
        "_buildAndroidCell",
        "_buildAzRail",
        "_renderRoomTabs",
        "_note",
    )
    for name in builders:
        assert "innerHTML" not in _method(source, name), f"{name} scrive HTML grezzo"


def test_the_android_icon_src_accepts_only_data_images() -> None:
    body = _method(_src(), "_buildAndroidCell")
    assert "app.icon.startsWith('data:image/')" in body, (
        "`src` accetta anche schemi che eseguono, e l'icona ha fatto un giro "
        "fuori dal nostro codice"
    )


# ── quello che non deve regredire ───────────────────────────────────────────

def test_the_long_press_survives_in_all_three_rooms() -> None:
    source = _src()
    for name, sheet in (
        ("_buildJennyRow", "showJennyAppSheet"),
        ("_buildSkillRow", "showSkillSheet"),
        ("_buildAndroidCell", "showAndroidAppSheet"),
    ):
        body = _method(source, name)
        assert "setupLongPress" in body, f"{name}: la pressione lunga è sparita"
        assert sheet in body
        assert "dataset.longpress" in body, (
            f"{name}: senza il consumo del flag, il tap sintetico dopo la "
            "pressione lunga attiva anche la riga"
        )


def test_the_drawer_contract_is_untouched_by_the_wider_skill_list() -> None:
    """`loadSkills` ora tiene anche le skill di serie: il cassetto non deve
    accorgersene — è un lanciatore, e le skill non si lanciano."""
    entries = _method(_src(), "launcherEntries")
    assert "this.skills" not in entries
    assert "kind: 'jenny'" in entries and "kind: 'android'" in entries
    assert "kind: 'skill'" not in entries


def test_the_accordion_left_nothing_behind() -> None:
    """Il CSS della fisarmonica non serve più a nessuno: lasciarlo lì sarebbe
    un pezzo di foglio di stile che nessun selettore raggiunge, e la prossima
    persona non avrebbe modo di saperlo."""
    css = CSS.read_text(encoding="utf-8")
    for dead in (
        ".apps-section",
        ".apps-chevron",
        ".app-badge",
        ".ab-active",
        ".ab-idle",
        ".app-error",
    ):
        assert dead not in css, f"{dead} è orfano e va tolto, non lasciato marcire"
    source = _src()
    assert "collapsedSections" not in source
    it = json.loads((ASSETS / "i18n" / "it.json").read_text(encoding="utf-8"))
    for dead_key in ("jennyApps", "skill", "androidApp", "active", "idle", "broken"):
        assert dead_key not in it["apps"], (
            f"apps.{dead_key} era l'etichetta della fisarmonica o del badge: "
            "nessuno la chiede più"
        )


def test_the_mascot_does_not_eat_taps_in_the_apps_tab() -> None:
    """La mascotte è ancorata al bordo destro e la sua sagoma copre i ~53 px
    più a destra, dove ora stanno gli interruttori delle skill. Misurato sul
    Titan 2: tre tocchi su tre aprivano la minichat invece di muovere
    l'interruttore. Resta visibile, ma in questa vista non prende i tocchi.
    """
    css = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    assert ":root.mode-apps .jenny-duo { pointer-events: none; }" in css
    # La regola dipende da una classe che `switchMode` deve scrivere davvero.
    src = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    assert "classList.add(`mode-${mode}`)" in src, (
        "senza la classe su <html> la regola non si applica mai, e il test "
        "sopra resterebbe verde a difetto presente"
    )
    assert "if (c.startsWith('mode-')) document.documentElement.classList.remove(c)" in src, (
        "la classe della vista precedente va tolta, o si accumulano"
    )
