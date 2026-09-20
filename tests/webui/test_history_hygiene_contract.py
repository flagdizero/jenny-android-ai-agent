"""Igiene della history: lo stack cala, e la posizione di lettura sopravvive.

Quattro difetti con la stessa radice — qualcuno *scrive* nella history mentre
l'utente sta andando indietro — e due con la stessa forma: la posizione di
lettura di un contenitore che scorre viene buttata via da un re-render o da un
cambio sezione.

- la freccia ← dell'editor del workspace impilava una entry *in avanti* per
  tornare alla sezione d'origine, cioè l'opposto del tasto Indietro, che con lo
  stesso stato è scritto apposta per non impilare;
- Home impilava la schermata iniziale invece di collassarci: la home diventava
  annullabile con Indietro (nessun launcher lo fa) e lo stack non calava mai;
- una entry non nostra (``state === null``, la lasciano dietro le ancore interne
  e le mini-app) faceva uscire il ``popstate`` in silenzio, senza aggiornare
  ``_navPos`` e senza cambiare niente a schermo;
- la radice veniva marcata *dopo* i due await del boot, quindi un tap sul dock
  durante l'avvio impilava una entry che poi veniva riscritta con pos 0 —
  pressione annullata da sé, ed entry mai contata sotto.

Il fix ovvio della posizione di lettura **non funziona** ed è il motivo per cui
qui si asserisce dove la misura viene presa: ``switchMode`` mette il
``display: none`` sulla view *prima* di chiamare ``deactivate()``, e un
contenitore senza box legge ``scrollTop``/``scrollHeight`` a 0. Salvare lì
avrebbe convertito "torna dov'eri" in "salta in fondo".

Asserzioni sul sorgente, nello stile di ``test_back_navigation_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "mobile-app.js"
CHAT_JS = ASSETS / "mobile-chat.js"
SETTINGS_JS = ASSETS / "mobile-settings.js"
WORKSPACE_JS = ASSETS / "mobile-workspace.js"


def _method(source: str, name: str) -> str:
    # `async ` opzionale: alcuni dei metodi pinnati qui sono caricatori asincroni.
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


def _app() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ── #12b · la entry non nostra ──────────────────────────────────────────


def test_a_foreign_history_entry_keeps_the_back_walking() -> None:
    """``state === null`` non è "non fare niente": è "questa entry non l'abbiamo
    scritta noi".

    Uscendo in silenzio la pressione spariva *e* ``_navPos`` restava indietro di
    uno per sempre, perché la entry sotto non veniva mai raggiunta. La radice ha
    sempre uno stato (``init`` la riscrive con ``replaceNav``), quindi
    proseguire all'indietro termina sempre su una entry nostra.
    """
    source = _app()
    popstate = re.search(
        r"window\.addEventListener\('popstate', \(e\) => \{(.*?)\n    \}\);", source, re.S
    )
    assert popstate, "listener popstate non trovato"
    body = popstate.group(1)
    guard = re.search(r"if \(!state\) \{(.*?)\}", body, re.S)
    assert guard, "manca il ramo esplicito per la entry non nostra"
    assert "this._navPos > 0" in guard.group(1), (
        "il fondo dello stack si riconosce dalla posizione nostra, non da history.length"
    )
    assert "window.history.back()" in guard.group(1), (
        "sulla entry non nostra il back deve proseguire, non uscire in silenzio"
    )


# ── #13 · la freccia ← non impila in avanti ─────────────────────────────


def test_leaving_the_editor_returns_to_the_origin_without_stacking() -> None:
    """L'editor aperto da un'altra sezione (App → modifica skill) ha la entry di
    quella sezione già nello stack, sotto la propria.

    Il back hardware lo sa e ritorna ``false`` apposta, lasciando che sia la
    history a riportare indietro. La freccia ← dell'header faceva l'opposto:
    ``switchMode(ret)``, con push di default, impilava una entry *in avanti*
    mentre l'utente stava tornando indietro.
    """
    workspace = WORKSPACE_JS.read_text(encoding="utf-8")
    # I commenti citano il prima: qui interessa solo il codice.
    close_editor = re.sub(r"//.*", "", _method(workspace, "_closeEditor"))
    assert "window.mobileApp?.navigateBack(ret);" in close_editor
    assert "switchMode(ret)" not in close_editor, (
        "switchMode con push di default impila una entry in avanti mentre si va indietro"
    )

    back = _method(_app(), "navigateBack")
    assert "this._navPos > 0" in back and "window.history.back()" in back, (
        "con una entry nostra sotto si torna indietro davvero, non si impila"
    )
    assert "this.switchMode(fallbackMode, false)" in back, (
        "alla radice non c'è niente sotto: si atterra senza impilare"
    )
    assert "this.replaceNav(" in back, "e la entry corrente deve descrivere la vista che resta"


# ── #14 · Home collassa alla radice ─────────────────────────────────────


def test_home_collapses_to_the_root_instead_of_stacking_over_it() -> None:
    """Home è la schermata iniziale, non una tappa in più.

    Con ``switchMode(target)`` (push) Indietro dopo Home riportava alla vista da
    cui si era usciti — nessun launcher si comporta così — e lo stack non calava
    mai: dieci Home, dieci entry da smaltire una pressione alla volta.
    """
    body = _method(_app(), "goHome")
    assert "this.switchMode(target, false);" in body, "la vista home non si impila"
    assert "this._navPos = 0;" in body, "Home riporta al fondo dello stack nostro"
    assert "this.replaceNav(" in body, "la entry corrente deve descrivere la home"
    assert body.index("this.switchMode(target, false);") < body.index("this._navPos = 0;"), (
        "prima si cambia vista, poi si marca la radice: replaceNav descrive dove si è atterrati"
    )
    assert "target === 'last'" in body, (
        "'last' vuol dire 'lasciami dove sono': niente cambio vista e niente riscrittura"
    )


def test_the_workspace_editor_is_never_dropped_behind_home() -> None:
    """Il reset del sotto-stato del workspace, se mai verrà agganciato a Home,
    deve passare dal teardown unico: è l'unico posto in cui il buffer sporco
    viene guardato, e prima ce n'erano due che non lo guardavano affatto."""
    app = _app()
    assert "_resetToExplorerAt" not in app, "la shell non smonta l'editor scavalcando il guard"
    assert "backToExplorer" not in app


# ── N23 · la radice marcata prima degli await del boot ──────────────────


def test_the_root_entry_is_marked_before_the_boot_awaits() -> None:
    """I listener del dock sono attivi da subito: durante i due await del boot
    un tap impilava la propria entry sopra una radice non ancora marcata, e la
    marcatura tardiva la riscriveva con pos 0 — vista riportata indietro da sé,
    pressione annullata in silenzio e una entry mai contata sotto.

    Le alternative (flag ``_booted``, listener registrati dopo) lasciano il tap
    *senza risposta*: qui invece viene onorato.

    **Aggiornato.** Le asserzioni citavano righe intere del boot, nomi delle
    variabili locali compresi (``initialWiki``, ``const settings = ...``):
    rinominare una locale — che non cambia niente per nessuno — le faceva
    fallire. Il contratto è un *ordine*, e si verifica confrontando posizioni.
    """
    source = _app()
    init = re.search(r"\n  async init\(\)\s*\{(.*?)\n  \}", source, re.S)
    assert init, "init() non trovato"
    body = init.group(1)

    marks = [m.start() for m in re.finditer(r"this\.replaceNav\(this\._navStateFor\(", body)]
    assert marks, "il boot non marca più la radice"
    mark = marks[0]
    # I due await che aprono la finestra: non sono variabili locali, sono i
    # nomi delle chiamate — rinominarli *è* un cambio di contratto.
    for await_call in (r"await api\.getSettings\(\)", r"await this\._initSessions\(\)"):
        found = re.search(await_call, body)
        assert found, f"await sparito dal boot: {await_call}"
        assert mark < found.start(), (
            "la radice va marcata prima degli await, altrimenti un tap sul dock la scavalca"
        )

    assert "if (!this.currentMode || this._firstRun) {" in body, (
        "un tap durante il boot ha già scelto la vista: va onorato, non annullato "
        "(il primo avvio è l'eccezione, lì la navigazione è bloccata)"
    )


# ── N11 · la posizione di lettura della chat ────────────────────────────


def test_the_chat_anchor_is_measured_while_the_view_is_still_visible() -> None:
    """Il fix ovvio — misurare in ``deactivate()`` — non funziona.

    ``switchMode`` mette il ``display: none`` sulla view *prima* di chiamare
    ``deactivate()``, e un contenitore senza box legge ``scrollTop`` e
    ``scrollHeight`` a 0: l'ancora varrebbe sempre 0, cioè "vai in fondo".
    La misura va presa nel listener ``scroll`` che già esiste.
    """
    chat = CHAT_JS.read_text(encoding="utf-8")
    listener = re.search(
        r"window\.addEventListener\('scroll', \(\) => \{(.*?)\}, \{ passive: true \}\);",
        chat,
        re.S,
    )
    assert listener, "listener scroll della chat non trovato"
    assert "this._rememberScrollAnchor();" in listener.group(1)

    remember = _method(chat, "_rememberScrollAnchor")
    assert "this._scroller.scrollHeight - this._scroller.scrollTop" in remember, (
        "l'ancora è la distanza dal fondo: scrollTop da solo scivola se arrivano messaggi"
    )
    assert "clientHeight" in remember, (
        "a vista nascosta 0 non è una posizione di lettura, è l'assenza di un box"
    )

    deactivate = _method(chat, "deactivate")
    assert "scrollTop" not in deactivate and "scrollHeight" not in deactivate, (
        "deactivate() è invocato dopo il display:none: lì la misura vale sempre 0"
    )


def test_the_chat_restores_the_reading_position_and_the_fab_with_it() -> None:
    """Chi era risalito a leggere tornava sempre in fondo, e doveva riscorrere
    tutto. Il ripristino va al primo rAF utile — a display appena ripristinato
    le altezze non sono ancora quelle definitive — e riallinea la FAB, che
    altrimenti resterebbe a raccontare uno stato che non è più quello."""
    chat = CHAT_JS.read_text(encoding="utf-8")
    activate = _method(chat, "activate")
    assert "this._restoreScrollAnchor();" in activate
    assert "if (this._autoScroll) this.scrollToBottom(true);" in activate, (
        "chi era già in fondo ci resta: l'ancora non deve fargli perdere i messaggi nuovi"
    )

    restore = _method(chat, "_restoreScrollAnchor")
    assert "requestAnimationFrame" in restore
    assert "if (!this._active) return;" in restore, "usciti di nuovo, non si tocca più niente"
    assert "this._scroller.scrollHeight - anchor" in restore
    assert "this._updateScrollFab();" in restore, "la FAB va riallineata alla posizione ripristinata"


# ── N19 (scroll) · le impostazioni ──────────────────────────────────────


def test_the_settings_keep_their_scroll_across_re_renders() -> None:
    """``render()`` rifà l'innerHTML del contenitore che scorre: ogni
    salvataggio riportava in cima una pagina lunga. Stessa forma della chat, e
    stesso motivo per cui la misura non può stare in ``deactivate()``."""
    settings = SETTINGS_JS.read_text(encoding="utf-8")
    assert "this.contentEl?.addEventListener('scroll'" in settings, (
        "la posizione va letta mentre la vista è visibile, non a display:none fatto"
    )
    assert "if (this.contentEl.clientHeight) this._scrollTop = this.contentEl.scrollTop;" in settings

    deactivate = _method(settings, "deactivate")
    assert "scrollTop" not in deactivate

    render = _method(settings, "render")
    assert "this._restoreScrollTop();" in render
    assert render.index("this._wireSections();") < render.index("this._restoreScrollTop();"), (
        "il ripristino va in coda, quando l'HTML nuovo è già a posto"
    )


def test_the_settings_scroll_restore_does_not_destroy_what_it_restores() -> None:
    """La prima stesura del ripristino si auto-distruggeva, e l'asserzione «sta
    in coda a render()» era vera lo stesso — per questo il difetto è passato.

    Il meccanismo: ``render()`` rimette la posizione mentre blocco SSH e lista
    snapshot sono ancora dei «Caricamento…». La pagina è
    molto più corta di quando la posizione fu misurata, quindi Blink clampa
    l'assegnazione a ``scrollHeight - clientHeight``; l'assegnazione emette un
    evento ``scroll``, e il listener del costruttore riscriveva ``_scrollTop``
    col valore clampato. La posizione buona non era approssimata: era persa, e
    quando i gruppi del catalogo si riempivano un attimo dopo non la
    riapplicava nessuno. Lo scenario che lo motivava — scegliere un modello a
    catalogo aperto e pagina scorsa in basso — adesso vive in casa; il
    meccanismo resta perché SSH e la storia degli snapshot atterrano allo
    stesso modo.

    Due proprietà, quindi: il listener non registra le scritture nostre, e i
    caricatori asincroni riapplicano quando il contenuto è atterrato.
    """
    settings = SETTINGS_JS.read_text(encoding="utf-8")

    listener = settings[settings.index("this.contentEl?.addEventListener('scroll'") :][:600]
    guard = "if (this._restoringScroll) return;"
    assert guard in listener, "il listener deve ignorare le scritture del ripristino"
    assert listener.index(guard) < listener.index("this._scrollTop = this.contentEl.scrollTop;"), (
        "la guardia deve precedere la scrittura, altrimenti non guarda niente"
    )

    restore = _method(settings, "_restoreScrollTop")
    assert "this._restoringScroll = true;" in restore
    assert restore.index("this._restoringScroll = true;") < restore.index(
        "this.contentEl.scrollTop = this._scrollTop;"
    ), "il flag va alzato prima di scrivere: l'evento arriva dopo"
    assert "requestAnimationFrame" in restore, (
        "il flag si azzera a evento smaltito e la posizione si riapplica a layout assestato"
    )
    assert "this._restorePending" in restore, (
        "se l'utente ha scorso di suo, un fetch in ritardo non deve strattonarlo"
    )

    # Il contenuto asincrono atterra dopo: chi lo inietta riapplica.
    # `_fillCatalogGroup` non c'e' piu': il catalogo modelli e' in casa dal
    # 20/09/2026, e con lui se n'e' andato il caso che dava il nome a N19.
    for name in ("_loadSsh", "_loadSnapshotList"):
        assert "this._restoreScrollTop();" in _method(settings, name), (
            f"{name}() allunga la pagina dopo il ripristino: deve riapplicarlo"
        )


def test_the_model_catalog_moved_to_the_casa_with_its_promise() -> None:
    """Il catalogo modelli non e' piu' in officina: e' in casa, da «Chi
    risponde», dal 20/09/2026 (`.agent/officina-tavole-plan.md`, passo 3).

    Questo banco difendeva lo stato «aperto + filtro» attraverso il
    ridisegno, perche' scegliere un modello *e'* un salvataggio e il
    salvataggio ridisegna. In casa quel problema non si pone nella stessa
    forma — i cataloghi gia' chiesti vivono nel controller e non nel DOM — ma
    la promessa va tenuta da qualche parte, ed e' qui che si dice dov'e'
    andata: `test_casa_model_client.py`, «il catalogo si chiede una volta per
    provider» e «un catalogo in ritardo non dipinge sopra quello che stai
    leggendo».

    Quel che si misura adesso e' il confine: l'officina non deve riprenderselo.
    """
    settings = SETTINGS_JS.read_text(encoding="utf-8")
    for pezzo in ("model-catalog", "btn-change-model", "_loadModelCatalog", "_selectModel"):
        assert pezzo not in settings, (
            f"«{pezzo}» e' tornato in officina: la scelta del modello e' in casa"
        )
    casa = (SETTINGS_JS.parent / "casa-model.js").read_text(encoding="utf-8")
    assert "getProviderModels" in casa and "default_provider" in casa, (
        "la casa non ha piu' il catalogo: toglierlo dall'officina lo toglierebbe "
        "dall'app"
    )
