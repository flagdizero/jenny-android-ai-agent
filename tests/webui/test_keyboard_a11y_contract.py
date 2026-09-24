"""Tastiera fisica e TalkBack: niente tappe cieche, niente nodi fantasma.

Sul Titan 2 la tastiera QWERTY è sempre sotto le dita, quindi Tab è un mezzo di
navigazione primario e non un caso di nicchia. Quattro difetti nascevano tutti
dallo stesso equivoco — "fuori dalla vista" scritto con proprietà che non
tolgono l'elemento né dalla tab order né dall'albero di accessibilità:

* lo scrim della minichat è un ``<button>`` a schermo intero con ``aria-label``,
  nascosto con ``opacity: 0`` + ``pointer-events: none``: una tappa Tab cieca
  (l'anello di fuoco è invisibile) e un "Chiudi minichat, pulsante" letto da
  TalkBack su OGNI schermata. Che il problema fosse noto lo dimostra il
  ``tabindex="-1"`` messo sul ``.jenny-duo`` accanto, che è lo stesso tipo di
  elemento;
* i ``.drawer`` sono fuori schermo per solo ``transform``, e il loro markup è
  statico in tutte le sezioni (sono apribili solo nella wiki): da chiusi
  restavano tabbabili e leggibili ovunque, da aperti non rendevano inerte
  niente;
* le celle della griglia App sono ``<div>`` senza ``tabindex`` né gestione di
  Invio/Spazio: la sezione App non era utilizzabile da tastiera per nulla — ed è
  il prerequisito perché il ritorno del fuoco dopo un overlay abbia un senso;
* il long-press del Workspace conservava la copia pre-estrazione dell'helper
  ``shared/longpress.js`` (nessuna soglia di movimento: il micro-tremore del
  dito durante i 600 ms annullava), senza il guard che consuma il flag e senza
  la finestra di grazia sul backdrop degli sheet.

Asserzioni sul sorgente, nello stile di ``test_back_navigation_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
CSS = ASSETS / "mobile-style.css"


def _read(*parts: str) -> str:
    return ASSETS.joinpath(*parts).read_text(encoding="utf-8")


def _css_rule(selector: str) -> str:
    """Corpo della regola CSS con quel selettore esatto (blocco fino a `}` a
    inizio riga)."""
    match = re.search(
        rf"^{re.escape(selector)}\s*\{{(.*?)\n\}}", CSS.read_text(encoding="utf-8"), re.S | re.M
    )
    assert match, f"regola CSS non trovata: {selector}"
    return match.group(1)


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  {re.escape(name)}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"metodo {name} non trovato"
    return body.group(1)


def _first_party_js() -> list[Path]:
    return [p for p in sorted(ASSETS.rglob("*.js")) if "vendor" not in p.parts]


# ── N14 · scrim della minichat ────────────────────────────────────────────────


def test_the_minichat_scrim_is_not_a_blind_tab_stop() -> None:
    """Da chiuso lo scrim deve sparire anche per Tab e per TalkBack.

    ``opacity: 0`` + ``pointer-events: none`` tolgono il colore e il click, non
    la focalizzabilità: il ``<button>`` restava una tappa Tab su ogni schermata,
    con l'anello di fuoco invisibile, e un nodo letto ad alta voce.
    """
    base = _css_rule(".jenny-scrim")
    assert "visibility: hidden" in base, "lo scrim chiuso resta focalizzabile e leggibile"
    opened = _css_rule(".jenny-scrim.open")
    assert "visibility: visible" in opened, "aperto lo scrim deve tornare visibile e cliccabile"


def test_hiding_the_scrim_does_not_eat_its_fade_out() -> None:
    """La ``visibility`` non si interpola: senza ritardo, alla chiusura lo scrim
    scomparirebbe di colpo invece di dissolversi."""
    base = _css_rule(".jenny-scrim")
    assert re.search(r"visibility 0s linear 0\.25s", base), (
        "la visibility deve cambiare solo a dissolvenza finita"
    )
    assert re.search(r"visibility 0s linear 0s", _css_rule(".jenny-scrim.open")), (
        "in apertura invece deve essere immediata, o non si vedrebbe nemmeno l'entrata"
    )


# ── N13 · drawer ──────────────────────────────────────────────────────────────


def test_a_closed_drawer_is_out_of_the_tab_order() -> None:
    """Il markup dei drawer è statico e presente in tutte le sezioni, anche dove
    non sono apribili: fuori schermo per solo ``transform`` restavano tabbabili
    e leggibili da TalkBack ovunque."""
    base = _css_rule(".drawer")
    assert "visibility: hidden" in base
    assert "visibility 0s linear .32s" in base, (
        "senza ritardo la chiusura del drawer non si vedrebbe scorrere via"
    )
    opened = _css_rule(".drawer.open")
    assert "visibility: visible" in opened
    assert "visibility 0s linear 0s" in opened


def test_an_open_drawer_makes_what_is_underneath_inert() -> None:
    """Da aperto il drawer copre la vista ma non la disattivava: nessun
    ``inert``, nessun ``aria-hidden``, nessun focus trap — Tab proseguiva nel
    contenuto coperto e nel dock."""
    drawer = _read("mobile-drawer.js")
    assert "this._setContentInert(true)" in _method(drawer, "open")

    inert = _method(drawer, "_setContentInert")
    assert "child.inert = on" in inert
    assert "classList.contains('drawer')" in inert, "il drawer non deve rendere inerte sé stesso"
    assert "child === this.backdrop" in inert, (
        "il backdrop è il modo primario di richiudere: renderlo inerte lo bloccherebbe"
    )
    assert "'.dock'" not in inert, (
        "il dock vive fuori da .main, resta visibile sotto il drawer e continua a funzionare: "
        "renderlo inerte disattiverebbe un comando che si vede"
    )


def test_the_drawer_takes_the_focus_and_gives_it_back() -> None:
    """Reso inerte il contenuto, il fuoco resterebbe su un nodo disattivato:
    deve entrare nel drawer e tornare all'invocante alla chiusura."""
    drawer = _read("mobile-drawer.js")
    opened = _method(drawer, "open")
    assert "this._lastFocus = document.activeElement" in opened
    assert ".drawer-close" in opened and "focus" in opened

    for name in ("close", "closeAll"):
        assert "this._releaseContent()" in _method(drawer, name), (
            f"{name} lascia lo sfondo inerte e il fuoco nel nulla"
        )

    release = _method(drawer, "_releaseContent")
    assert "this._setContentInert(false)" in release
    assert "isConnected" in release, (
        "la vista può essere stata ridisegnata: il fuoco non si sposta su un nodo staccato"
    )


def test_switching_drawers_is_not_a_return_to_the_view() -> None:
    """Passando da un drawer all'altro il fuoco non deve rimbalzare
    sull'invocante per poi rientrare subito nel drawer nuovo."""
    drawer = _read("mobile-drawer.js")
    opened = _method(drawer, "open")
    assert "this._swapping = true" in opened
    assert "if (!this._swapping) this._releaseContent()" in _method(drawer, "close")


def test_the_lightbox_takes_the_focus_and_gives_it_back() -> None:
    """La lightbox copre tutto ma non rende inerte ciò che sta sotto: senza
    portare il fuoco sulla chiusura, Tab proseguiva nella pagina coperta."""
    source = _read("shared", "image-lightbox.js")
    assert "const previousFocus = document.activeElement;" in source
    assert "closeBtn.focus();" in source
    assert source.index("document.body.appendChild(overlay);") < source.index("closeBtn.focus();"), (
        "il fuoco si può dare solo a un elemento già nel documento"
    )
    assert "previousFocus.isConnected" in source, (
        "al ritorno l'invocante può non esserci più: in quel caso non si sposta niente"
    )


# ── N3 · un solo long-press, e chi lo chiama lo consuma ───────────────────────


def test_no_asset_outside_shared_implements_its_own_long_press() -> None:
    """``shared/longpress.js`` dichiara in testa di essere stato estratto dal
    Workspace, che però conservava la copia pre-estrazione — priva della soglia
    di movimento, quindi annullata da qualunque ``pointermove``: il micro-tremore
    del dito durante i 600 ms bastava."""
    owner = ASSETS / "shared" / "longpress.js"
    assert "MOVE_THRESHOLD" in owner.read_text(encoding="utf-8"), (
        "la soglia di movimento è la ragione per cui la copia locale era sbagliata"
    )

    for path in _first_party_js():
        if path == owner:
            continue
        source = path.read_text(encoding="utf-8")
        assert "dataset.longpress = " not in source, (
            f"{path.name} posa il flag da sé: il long-press è di shared/longpress.js"
        )
        assert not re.search(r"_?setupLongPress\s*\([^)]*\)\s*\{", source), (
            f"{path.name} definisce un proprio long-press"
        )


def test_every_long_press_caller_consumes_the_flag() -> None:
    """Il flag lo posava il long-press e non lo leggeva nessuno: il tap
    sintetico che segue la pressione navigava nella cartella (o apriva il file)
    *sotto* lo sheet appena comparso."""
    callers = 0
    for path in _first_party_js():
        if path.name == "longpress.js":
            continue
        source = path.read_text(encoding="utf-8")
        calls = len(re.findall(r"\bsetupLongPress\(", source))
        if not calls:
            continue
        callers += 1
        assert "import { setupLongPress }" in source, f"{path.name} non importa l'helper condiviso"
        # `[\w.]+` e non `\w+`: la guardia puo' stare su un campo dell'oggetto
        # (`this.door.dataset.longpress`) e non solo su una variabile locale.
        guards = len(re.findall(r"if \([\w.]+\.dataset\.longpress\)", source))
        assert guards == calls, (
            f"{path.name}: {calls} long-press ma {guards} guardie nei click handler"
        )
    assert callers >= 2, "i chiamanti attesi sono almeno la sezione App e il Workspace"


def test_the_workspace_sheets_ignore_the_synthetic_tap() -> None:
    """Il tap sintetico che segue il long-press arriva sul backdrop del
    ``<dialog>`` appena aperto e lo richiudeva all'istante. La sezione App aveva
    già la finestra di grazia; i due sheet del Workspace no."""
    source = _read("mobile-workspace.js")
    # I due menu montano il foglio da `_apriFoglio`; il comportamento vero è
    # in `test_workspace_sheets_client.py`.
    for name in ("showContextSheet", "_showNewMenu"):
        assert "this._apriFoglio(" in _method(source, name), f"{name} monta il foglio da sé"
    body = _method(source, "_apriFoglio")
    assert "const openedAt = Date.now();" in body, "il foglio non misura da quando è aperto"
    assert "Date.now() - openedAt > 400" in body, "il foglio non ha la finestra di grazia"
    assert "sheet.addEventListener('click'" not in body, (
        "il foglio conserva il vecchio listener del backdrop senza finestra di grazia"
    )
    assert "sheet.onclick = null" in body, "il gestore del backdrop va sganciato alla chiusura"
