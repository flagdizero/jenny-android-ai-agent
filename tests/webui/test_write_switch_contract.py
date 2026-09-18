"""L'interruttore scrittura / sola lettura, dal lato del client.

Passo **4.5** di ``roadmap/progetti-passi.md``.

Risponde alla seconda metà della stessa domanda del chip — *cosa farà quel che
sto per mandare* — e per questo sta nella stessa riga: un messaggio partito
credendolo in sola lettura non si ritira, e l'unico istante in cui l'utente può
accorgersene è mentre guarda il composer.

Le tre proprietà che tengono in piedi quella promessa, e sono tutte la stessa
proprietà vista da tre lati:

1. **il flag parte con ogni messaggio**, e il server non lo tiene. Un server che
   lo tenesse potrebbe applicare al turno un modo diverso da quello che l'utente
   aveva sotto gli occhi;
2. **si vede senza aprire niente.** Esisteva già un display dell'access mode, ma
   dentro il popover "Info sessione", che non è un interruttore che si vede;
3. **lo dice anche il composer**, perché il placeholder è l'ultima cosa che
   l'occhio attraversa prima di premere invio.

Asserzioni sul sorgente, come ``test_scope_menu_contract.py`` e
``test_project_views_contract.py``: la WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"
SWITCH = ASSETS / "shared" / "write-switch.js"
WS = ASSETS / "shared" / "ws-manager.js"
CHIP = ASSETS / "shared" / "scope-chip.js"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(path: Path) -> str:
    """Il sorgente senza commenti.

    Serve perché più di un'asserzione qui sotto cerca l'*assenza* di qualcosa, e
    i commenti di questi file spiegano proprio le cose che non ci devono essere
    («non in localStorage», e via così). Già inciampato il 22/08 sul chip: v.
    ``test_scope_menu_contract.py``, dove un `assert not in` prendeva la riga
    che spiegava perché.
    """
    src = re.sub(r"/\*.*?\*/", "", _src(path), flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//.*$", "", src)


def _method(path: Path, name: str) -> str:
    body = re.search(rf"\n  (?:async |get )?{re.escape(name)}\([^)]*\)\s*\{{(.*?)\n  \}}",
                     _src(path), re.S)
    assert body, f"{name} non trovato in {path.name}"
    return body.group(1)


# ── Il flag parte con ogni messaggio ─────────────────────────────────────


def test_every_outgoing_message_carries_the_mode() -> None:
    """Non uno stato sul server: quel che vedi è quel che mandi."""
    body = _method(WS, "sendToChat")
    assert "AppState.readonlyTurn" in body
    assert "payload.readonly = true" in body


def test_only_true_is_sent() -> None:
    """Il gateway accende su ``True`` e ignora il resto: il client non deve mentirgli."""
    body = _method(WS, "sendToChat")
    assert re.search(r"readonlyTurn === true", body), (
        "il confronto deve essere stretto: un valore accidentalmente truthy manderebbe "
        "in sola lettura un turno che l'utente credeva scrivibile"
    )


def test_the_server_is_not_asked_to_remember_it() -> None:
    """Nessuna chiamata che *depositi* il modo: esiste solo nell'envelope."""
    src = _code(SWITCH)
    assert "localStorage" not in src, (
        "la conversazione aperta non sopravvive a un riavvio (session-manager riparte "
        "sempre dalla personale): una preferenza che sopravvive è una promessa senza soggetto"
    )
    assert "fetch(" not in src and "api." not in src, "il modo non si deposita da nessuna parte"


# ── Un solo scrittore, come per l'aggancio delle viste ───────────────────


def test_only_the_switch_publishes_the_mode() -> None:
    writers = [
        p.name
        for p in sorted(ASSETS.rglob("*.js"))
        if re.search(r"AppState\.set\(\s*['\"]readonlyTurn['\"]", _code(p))
    ]
    assert writers == ["write-switch.js"], (
        f"due scrittori sono due risposte alla stessa domanda; trovati: {writers}"
    )


# ── È per conversazione ──────────────────────────────────────────────────


def test_the_preference_is_per_conversation() -> None:
    """Un progetto parte scrivibile mentre la personale resta come l'avevi lasciata."""
    src = _src(SWITCH)
    assert "_byKey" in src, "la preferenza è indicizzata per chiave di sessione"
    assert "syncFromSession" in src
    body = _method(SWITCH, "get readonly")
    assert "_byKey.get(this._key)" in body
    assert "=== true" in body, "senza preferenza registrata si scrive: è il default"


# ── Si vede, e lo dice il composer ───────────────────────────────────────


def test_the_switch_sits_in_the_chip_row() -> None:
    """Nel popover Info sessione c'era già, e non contava come «si vede»."""
    html = (UI / "officina.html").read_text(encoding="utf-8")
    row = re.search(r'<div class="compose-scope">(.*?)</div>\s*<div id="input-row"',
                    html, re.S)
    assert row, "riga .compose-scope non trovata"
    assert 'id="write-switch"' in row.group(1)
    assert 'id="scope-chip"' in row.group(1), "chip e interruttore nella stessa riga"


def test_the_row_cannot_push_the_switch_off_screen() -> None:
    """Passo **6**: cosa fa il chip con un nome lungo, ora che la riga è in due.

    Un nome di progetto arriva a 64 caratteri (``is_valid_project_name``), quindi
    il caso non è teorico. ``max-width: 100%`` da solo non basta: quel 100% è
    della riga *intera*, e l'interruttore è ``flex-shrink: 0`` — il chip se la
    prendeva tutta e l'interruttore usciva a destra. ``min-width: 0`` è la metà
    che si dimentica: senza, un contenuto flex rifiuta di scendere sotto la
    propria larghezza intrinseca e lo shrink non morde.
    """
    css = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    chip = re.search(r"^\.scope-chip \{(.*?)^\}", css, re.S | re.M)
    assert chip, "regola .scope-chip non trovata"
    body = chip.group(1)
    assert re.search(r"flex:\s*0 1 auto", body), "il chip deve poter cedere"
    assert re.search(r"min-width:\s*0", body), "e poter scendere sotto la larghezza intrinseca"

    switch = re.search(r"^\.write-switch \{(.*?)^\}", css, re.S | re.M)
    assert switch, "regola .write-switch non trovata"
    assert re.search(r"flex-shrink:\s*0", switch.group(1)), (
        "l'interruttore non cede: è lui che deve restare leggibile"
    )

    crumb = re.search(r"^\.scope-chip-crumb \{(.*?)^\}", css, re.S | re.M)
    assert crumb, "regola .scope-chip-crumb non trovata"
    assert re.search(r"max-width:\s*\d+ch", crumb.group(1)), "la troncatura vera sta sul crumb"
    assert "ellipsis" in crumb.group(1)


def test_reduced_motion_covers_the_switch_too() -> None:
    """L'interruttore è nato dopo il blocco `prefers-reduced-motion`, e c'era rimasto fuori.

    Ha la stessa transizione del chip e lo stesso ``scale(0.98)`` al tocco. La
    transizione la spegne comunque la regola ``*`` in fondo al file
    (``transition-duration: 0.01ms !important``); il ``transform`` di uno stato
    ``:active`` **no** — non è né un'animazione né una durata — quindi il
    rimpicciolimento al tocco era l'unica cosa che restava, ed è la parte che
    quella preferenza chiede di togliere.
    """
    css = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
    blocks = re.findall(
        r"@media \(prefers-reduced-motion: reduce\) \{(.*?)^\}", css, re.S | re.M
    )
    assert blocks, "blocco prefers-reduced-motion non trovato"
    covered = "\n".join(blocks)
    assert re.search(r"\.write-switch:active \{[^}]*transform:\s*none", covered), (
        "il tocco rimpicciolisce l'interruttore anche a movimento ridotto"
    )
    # Il fratello nella stessa riga resta coperto: erano nello stesso blocco.
    assert re.search(r"\.scope-chip:active \{[^}]*transform:\s*none", covered)


def test_the_placeholder_shortens_a_long_project_name() -> None:
    """Il chip tronca in CSS, un placeholder di `<textarea>` no.

    Visto sul telefono il 22/08: con un nome lungo il placeholder sforava la sua
    scatola e veniva tagliato a metà parola **senza puntini**, che si legge come
    un testo rotto invece che accorciato.
    """
    src = _code(CHIP)
    assert "NAME_IN_PLACEHOLDER" in src
    assert re.search(r"_short\(this\.scope\.name\)", src), (
        "il nome va accorciato prima di finire nel placeholder"
    )
    short = re.search(r"function _short\(name\) \{(.*?)\n\}", src, re.S)
    assert short, "_short non trovata"
    body = short.group(1)
    assert "NAME_IN_PLACEHOLDER" in body and "slice(" in body
    # **Nessun puntino aggiunto qui**: ce li ha già la stringa localizzata, e
    # raddoppiarli dava `zz-bordi-lunghissimo-……`. Si toglie invece il
    # separatore finale, così i puntini del template attaccano a una parola.
    assert "…" not in body, "i puntini vengono dal template, non da qui"
    assert re.search(r"replace\(/\[-\._\]\+\$/", body), (
        "il separatore finale va tolto, o i puntini del template attaccano a un trattino"
    )
    for lang in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        assert data["scope"]["askAbout"].rstrip().endswith(("...", "…")), (
            f"{lang}: se il template perde i puntini, un nome tagliato non si vede più"
        )


def test_the_placeholder_repeats_it() -> None:
    """L'ultima cosa che l'occhio attraversa prima di premere invio."""
    body = _method(CHIP, "syncPlaceholder")
    assert "AppState.readonlyTurn" in body
    assert "write.askReadonly" in body
    assert "write.askAboutReadonly" in body, "vale anche dentro un progetto"


def test_the_placeholder_is_wired_to_the_change_and_not_to_the_order() -> None:
    """Due proprietari, un testo: iscriversi rende l'ordine dei sync irrilevante.

    Chip e interruttore si sincronizzano entrambi al caricamento del thread; con
    una chiamata diretta, chi arriva secondo lascia il testo del primo.
    """
    src = _src(CHIP)
    assert re.search(r"AppState\.on\('readonlyTurn',", src)


def test_both_states_are_named_in_both_languages() -> None:
    for lang in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        block = data["write"]
        for key in ("write", "readonly", "change", "writeHint", "readonlyHint",
                    "askReadonly", "askAboutReadonly"):
            assert block.get(key), f"{lang}: manca write.{key}"
        assert "{name}" in block["askAboutReadonly"]


def test_the_module_is_in_the_ui_manifest() -> None:
    """Fuori dal manifest il file non arriva sul telefono, e l'interruttore non c'è."""
    from jenny.utils.android_assets import _UI_MANIFEST

    assert "assets/shared/write-switch.js" in _UI_MANIFEST
