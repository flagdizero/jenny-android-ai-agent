"""Il lavoro non salvato non sparisce senza che l'utente lo sappia.

Tre difetti con la stessa forma — qualcosa che l'utente ha scritto viene buttato
via da un percorso che non sa di doverlo chiedere — e tre cinture diverse:

* **Editor del workspace.** Lo smontava chiunque: ``handleBack``, il ramo
  ``ws-back`` dell'header, i crumb del breadcrumb (visibili anche mentre si
  modifica un file) e il listener ``advancedmodechange`` del costruttore, che
  chiamava ``navigateTo`` — la quale forza ``viewMode = 'explorer'``
  incondizionatamente. Nessuno dei quattro guardava se il buffer era sporco: il
  segnale esisteva solo come classe CSS ``dirty`` sul pulsante Salva, e ``grep
  dirty`` sul file non trovava altro. Il testo modificato restava in un viewer
  nascosto irraggiungibile, e riaprire il file rifaceva la fetch
  sovrascrivendolo.

* **Dialog della passphrase** (``shared/backup-flow.js``). Risolveva la propria
  Promise solo su ``cancel`` o sul bottone: qualunque altra chiusura la lasciava
  appesa per sempre, e con lei ``_busy`` a true — export, import e restore
  morivano in silenzio **per tutta la vita della pagina**.

* **Salvataggio di un provider** (``mobile-settings.js``). ``_saveProvider`` non
  attendeva e non marcava il dialog: un Indietro durante la richiesta
  dispatchava un ``cancel`` che nessuno preveniva, il listener ``close`` faceva
  ``remove()``, e la chiave API appena digitata se ne andava col DOM lasciando
  solo un toast.

Asserzioni sul sorgente, come il resto di ``tests/webui/``: la WebUI non ha un
runner JS con DOM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
WORKSPACE_JS = ASSETS / "mobile-workspace.js"
SETTINGS_JS = ASSETS / "mobile-settings.js"
BACKUP_JS = ASSETS / "shared" / "backup-flow.js"

_METHOD_START = re.compile(r"\n  (?:async )?([A-Za-z_]\w*)\([^)]*\)\s*\{")


def _methods(source: str) -> dict[str, str]:
    """Corpo di ogni metodo della classe, indicizzato per nome.

    Serve a domandare *quali* metodi contengono una certa chiamata: è l'unico
    modo, senza un runtime, di dire "questa strada la percorre solo lui".
    """
    starts = [(m.start(), m.group(1)) for m in _METHOD_START.finditer(source)]
    bodies: dict[str, str] = {}
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(source)
        bodies[name] = source[pos:end]
    return bodies


def _workspace() -> str:
    return WORKSPACE_JS.read_text(encoding="utf-8")


# ── 1a. Editor del workspace ────────────────────────────────────────────────


def test_the_dirty_buffer_is_state_not_a_css_class() -> None:
    """Il segnale c'era già ma viveva nel DOM (``btn.classList.add('dirty')``):
    illeggibile da chi deve decidere se chiudere. Promosso a stato, va acceso
    dove il testo cambia e spento dove il testo torna sul disco o esce di
    scena."""
    methods = _methods(_workspace())

    assert "this._dirty = true;" in methods["renderCodeViewer"], (
        "senza il listener change di CodeMirror lo stato non si accende mai"
    )
    for name in ("saveFile", "_enterEditorView", "_resetToExplorerAt"):
        assert "this._dirty = false;" in methods[name], f"{name} deve azzerare il buffer sporco"

    setters = {n for n, b in methods.items() if "this._dirty = true" in b}
    assert setters == {"renderCodeViewer"}, (
        f"il buffer sporco si accende in un posto solo, non in {sorted(setters)}"
    )


def test_the_editor_is_dismantled_from_exactly_one_place() -> None:
    """Lo smontaggio meccanico (``_resetToExplorerAt``) è privato apposta: se lo
    chiamano in tanti, il guard sul buffer sporco lo si dimentica in uno di
    quelli — che è precisamente com'è andata."""
    methods = _methods(_workspace())

    callers = {n for n, b in methods.items() if "this._resetToExplorerAt(" in b}
    assert callers == {"_closeEditor", "backToExplorerAt"}, (
        f"lo smontaggio dell'editor è raggiunto da {sorted(callers)}: deve passare dal teardown unico"
    )
    assert "this._closeEditor({ dir: dirPath })" in methods["backToExplorerAt"], (
        "con un editor aperto anche un crumb del breadcrumb è un'uscita: passa dal guard"
    )

    close_editor = methods["_closeEditor"]
    assert "if (this._dirty)" in close_editor, "il teardown unico esiste per questo guard"
    assert "this._confirmDiscard(" in close_editor


def test_a_dirty_buffer_consumes_the_press_and_keeps_the_editor_up() -> None:
    """Mentre la conferma è a schermo la pressione è già stata spesa: il
    cambiamento visibile è il dialog. Ritornare false qui manderebbe avanti la
    catena, e l'utente si troverebbe la conferma sopra un'altra schermata."""
    body = _methods(_workspace())["_closeEditor"]
    dirty_branch = body[body.index("if (this._dirty)"):]
    assert dirty_branch.index("return true;") < dirty_branch.index("this._resetToExplorerAt("), (
        "il ramo sporco deve uscire prima di smontare qualsiasi cosa"
    )


def test_the_discard_confirmation_reenters_the_same_teardown() -> None:
    """La risposta affermativa non smonta a mano: riazzera il buffer e ripassa
    dalla stessa porta. Un secondo smontaggio scritto qui sarebbe la terza
    strada, cioè il difetto che questa ondata chiude."""
    body = _methods(_workspace())["_confirmDiscard"]
    assert "this._dirty = false;" in body
    assert "this._closeEditor({ dir });" in body
    assert "this._resetToExplorerAt(" not in body
    assert "if (this.viewMode !== 'editor') return;" in body, (
        "fra la domanda e la risposta l'editor può essere già uscito da un altro percorso"
    )



def test_the_discard_prompt_is_localized_in_both_languages() -> None:
    """Nessuna stringa visibile hardcodata, e nessuna chiave presente in un file
    solo (in quel caso l'altra lingua mostrerebbe la chiave grezza)."""
    assert "i18n.t('workspace.discardConfirm')" in _workspace()
    for lang in ("it", "en"):
        data = json.loads((ASSETS / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        assert data["workspace"]["discardConfirm"].strip(), f"chiave mancante o vuota in {lang}.json"


# ── 1b. Dialog della passphrase ─────────────────────────────────────────────


def test_the_passphrase_dialog_always_settles_its_promise() -> None:
    """Una Promise appesa qui non è un dialog che resta aperto: è ``_busy`` che
    non torna mai false, cioè backup, import e restore morti in silenzio fino al
    reload. La cintura è la stessa di
    ``confirmDialog``/``detailDialog``/``promptDialog``: risolvere sull'evento
    ``close``, che copre ogni via d'uscita, non solo ``cancel``."""
    source = BACKUP_JS.read_text(encoding="utf-8")
    prompt = source[source.index("export function promptPassphrase"):source.index("export function showRestartDialog")]

    assert "dialog.addEventListener('close', () => done(null));" in prompt
    assert "if (settled) return;" in prompt, (
        "senza il guard la doppia risoluzione richiuderebbe/rimuoverebbe un dialog già smontato"
    )
    assert "_busy = false" in source, "sparita la mutua esclusione: questa cintura andrebbe ripensata"


# ── 1c. Salvataggio provider in volo ────────────────────────────────────────


def test_an_in_flight_provider_save_cannot_be_dismissed() -> None:
    """La finestra fra il tap su Salva e la risposta del gateway: lì il dialog
    non deve poter sparire, perché si porterebbe via la chiave API. Il flag vive
    sul dataset perché è il listener ``cancel`` a doverlo leggere, e il
    ``cancel`` è ciò che la catena della shell dispatcha."""
    source = SETTINGS_JS.read_text(encoding="utf-8")
    methods = _methods(source)
    save = methods["_saveProvider"]

    assert "async _saveProvider(" in source, "senza await non esiste nemmeno la finestra da proteggere"
    assert "dialog.dataset.busy = '1';" in save
    assert "b.disabled = true;" in save, "i bottoni restano premibili durante la richiesta"
    assert "} finally {" in save and "delete dialog.dataset.busy;" in save, (
        "il flag va ripulito anche quando la richiesta fallisce, altrimenti il dialog non si chiude più"
    )

    dialog = methods["_showAddProviderDialog"]
    assert "if (dialog.dataset.busy) e.preventDefault();" in dialog, (
        "il rifiuto del congedo è ciò che tiene in vita i campi durante il salvataggio"
    )
    # Fuori dalla finestra di salvataggio, scartare i campi su un cancel è la
    # semantica normale di una modale annullabile: non si tocca.
    assert "dialog.addEventListener('close', () => dialog.remove());" in dialog
