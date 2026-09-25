"""La configurazione guidata: quello che si digita non si perde, e da lì si esce.

Quattro difetti con la stessa radice — il wizard tratta il proprio stato come
se il tempo non passasse fra un render e l'altro:

* ``_goToStep0`` cambiava step senza catturare i campi, quindi il pulsante
  "Indietro" (e il tasto hardware, che ci finisce sopra) **cancellava nome
  provider, chiave API e base URL appena digitati**. Il gemello
  ``_goBackToStep1`` la cattura la faceva già: erano due funzioni sorelle con
  due comportamenti diversi;
* ``handleBack`` non guardava ``saving``: una pressione durante il salvataggio
  rimetteva a schermo un form che non ha più effetto, e la continuazione di
  ``_save()`` gli scriveva sopra lo step 3 un istante dopo;
* ``_loadModels`` non aveva token: la risposta in ritardo scriveva nel DOM
  dello step nuovo, o dal ramo d'errore riapriva il campo "modello
  personalizzato" su un form che non ce l'ha;
* il blocco del dock del primo avvio era **a senso unico**: ``grep -rn
  nav-disabled`` dava due righe, una che aggiungeva la classe e una che la
  leggeva, e nessuna che la togliesse — né toglieva ``pointer-events``,
  l'opacità o la voce onboarding dal dock.

In più il wizard non era riapribile: unica strada il ``first_run`` del gateway,
e se quella lettura falliva il boot trattava l'ignoto come "onboarding già
fatto" — cioè consumava il marcatore locale e portava in chat senza più alcun
modo di tornare al wizard.

Asserzioni sul sorgente, nello stile di ``test_back_navigation_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
ONBOARDING_JS = ASSETS / "mobile-onboarding.js"
APP_JS = ASSETS / "mobile-app.js"
SETTINGS_JS = ASSETS / "mobile-settings.js"
I18N = ASSETS / "i18n"


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


def _onboarding() -> str:
    return ONBOARDING_JS.read_text(encoding="utf-8")


def _app() -> str:
    return APP_JS.read_text(encoding="utf-8")


# ── #11 · i campi si catturano prima di cambiare step ────────────────────────


def test_going_back_to_step_zero_keeps_what_was_typed() -> None:
    """``_renderStep1`` ridisegna i campi *dallo stato*: ciò che non viene
    travasato prima del cambio step non esiste più. Su una tastiera fisica una
    chiave API è la cosa più scomoda da riscrivere, ed era la cosa che si
    perdeva più spesso — il tasto Indietro dell'onboarding porta proprio lì."""
    source = _onboarding()
    step0 = _method(source, "_goToStep0")
    assert "this._captureStep1()" in step0, (
        "il back allo step 0 cancellava nome provider, chiave API e base URL"
    )

    capture = _method(source, "_captureStep1")
    for field in ("#provider-name", "#api-key", "#api-base"):
        assert field in capture, f"campo non catturato: {field}"
    for target in ("this.providerName =", "this.apiKey =", "this.apiBase ="):
        assert target in capture

    # Avanti e indietro devono passare dallo stesso travaso: due copie
    # divergerebbero come è già successo fra _goToStep0 e _goBackToStep1.
    assert "this._captureStep1()" in _method(source, "_goToStep2")
    for path in ("_goToStep0", "_goToStep2"):
        assert "#api-key" not in _method(source, path), (
            f"{path} rilegge i campi per conto suo: è così che nasce il ramo che se ne dimentica uno"
        )


# ── #16 · nessun cambio di step mentre la config sta partendo ────────────────


def test_the_back_is_inert_while_the_configuration_is_being_saved() -> None:
    """Durante ``_save()`` c'è l'overlay di caricamento a schermo e lo step
    successivo è già deciso. La pressione si consuma senza fare niente: è
    l'unico caso in cui "una pressione, nessun cambiamento" va bene, perché il
    cambiamento è già in corso e visibile."""
    back = _method(_onboarding(), "handleBack")
    assert "if (this.saving) return true;" in back
    assert back.index("if (this.saving) return true;") < back.index("this.step === 1"), (
        "il guard deve stare in cima, prima di qualunque ramo che cambi step"
    )


# ── #23 · la fetch dei modelli sa di essere stata superata ───────────────────


def test_a_late_model_list_does_not_write_into_the_next_step() -> None:
    """Il token va controllato **in entrambi i rami**: il ramo d'errore non si
    limita a scrivere una riga, chiama ``_showCustomModelField()``, che accende
    uno stato del controller (``_showCustomModel``) e cerca un nodo che nello
    step nuovo non c'è."""
    source = _onboarding()
    body = _method(source, "_loadModels")
    assert "const token = ++this._modelsToken;" in body
    guards = body.count("if (token !== this._modelsToken) return;")
    assert guards == 2, f"il token è controllato {guards} volte invece di 2 (try e catch)"

    try_part, catch_part = body.split("} catch", 1)
    assert "if (token !== this._modelsToken) return;" in try_part
    assert "if (token !== this._modelsToken) return;" in catch_part

    # Chi esce dallo step 2 invalida la richiesta in volo, altrimenti il token
    # resta valido e la risposta arriva comunque a destinazione.
    for leaving in ("_goBackToStep1", "_goToStep0", "deactivate"):
        assert "this._modelsToken++" in _method(source, leaving), (
            f"{leaving} non invalida la fetch modelli in volo"
        )


# ── #17 / N17 · il blocco del dock si toglie con lo stesso interruttore ──────


def test_the_first_run_lock_is_a_single_two_way_switch() -> None:
    """Il blocco toccava quattro cose (classe, ``pointer-events``, opacità,
    voce onboarding) e nessuna aveva un ramo che la rimettesse a posto. Un
    blocco che non si sa togliere non è un blocco: è un danno che scade solo
    per fortuna (il reload che seguiva l'onboarding)."""
    source = _app()
    setter = _method(source, "_setFirstRunLock")
    assert "this._firstRun = !!on;" in setter, (
        "il flag e l'aspetto del dock devono muoversi insieme: erano due verità separate"
    )
    assert "classList.toggle('nav-disabled', !!on)" in setter
    assert "item.style.pointerEvents = on ? 'none' : '';" in setter
    assert "item.style.opacity = on ? '0.4' : '';" in setter
    assert "navOnb.style.display = on ? '' : 'none';" in setter

    # Nessun altro punto può alzare o abbassare il blocco a mano: una sola
    # scrittura (qui) e una sola lettura (_visibleModes, che salta le voci
    # spente quando calcola i vicini per lo swipe fra tab).
    assert source.count("'nav-disabled'") == 2, (
        "'nav-disabled' è scritto fuori dal setter: torna il blocco a senso unico"
    )
    assert "classList.add('nav-disabled')" not in source
    assert "contains('nav-disabled')" in _method(source, "_visibleModes")


def test_every_completion_of_the_onboarding_releases_the_lock() -> None:
    """Due percorsi mettono ``onboarding-complete`` mentre la pagina è viva: il
    "Fatto" del wizard e il **ripristino da backup**. Il secondo può concludersi
    senza ricaricare — il dialog di riavvio resta a schermo e si può rifiutare —
    e lì il dock restava spento con l'onboarding dato per concluso da tutto il
    resto."""
    source = _onboarding()
    for setter_call in re.finditer(r"localStorage\.setItem\('onboarding-complete'", source):
        window = source[setter_call.start(): setter_call.start() + 600]
        assert "_setFirstRunLock(false)" in window, (
            "un percorso completa l'onboarding senza togliere il blocco del dock"
        )


# ── N25 · una strada permanente verso il wizard ──────────────────────────────


def test_the_wizard_is_only_the_first_run() -> None:
    """«Riesegui la configurazione» non c'e' piu', ed e' una decisione.

    `save_onboarding` fa `config.providers.providers = [una]`: **sostituisce**
    l'elenco invece di aggiungere. In una schermata da operatore quel bottone
    puo' solo toglierti marche che hai configurato — e tutto cio' che il wizard
    imposta si fa meglio altrove: la marca col suo «Aggiungi», il modello dalla
    casa, il nome dalle impostazioni.

    **Aggiornato il 21/09/2026.** Quel giorno il bottone era sparito ma la
    strada per rientrarci no: restavano la porta (`openOnboarding`), il segnale
    «lo stai rifacendo» (`markRerun` → `_rerun`) e i due rami che ne
    dipendevano. Una porta senza maniglia da nessuno dei due lati: nessuno
    poteva aprirla, e chi leggeva il codice trovava una strada che non c'era.
    Peggio, un commento nel boot — proprio nel ramo che gestisce una Jenny
    rimasta senza provider — la prometteva a parole.

    Adesso l'unico modo di essere nel wizard e' il primo avvio, e il banco
    misura quello: non che manchi un bottone, ma che non esista **nessuna**
    strada oltre a quella.
    """
    settings = SETTINGS_JS.read_text(encoding="utf-8")
    assert "btn-rerun-onboarding" not in settings, (
        "il bottone e' tornato: in officina puo' solo togliere marche"
    )
    assert "_rerunOnboarding" not in settings

    app = _app()
    assert "openOnboarding" not in app, "la porta e' tornata senza una maniglia"
    assert "markRerun" not in _onboarding(), (
        "il wizard sa di nuovo di «essere rifatto», ma nessuno puo' rifarlo"
    )

    # L'unico ingresso: il primo avvio dirotta la vista iniziale.
    init = _method(app, "init")
    assert "initialMode = 'onboarding'" in init, (
        "il primo avvio non porta piu' al wizard: una Jenny nuova resta senza provider"
    )


@requires_node
def test_from_the_first_run_there_is_no_way_out() -> None:
    """Dal wizard del primo avvio non si esce col back, e ora e' senza
    eccezioni: sotto non c'e' niente, e una Jenny senza provider portata in chat
    non puo' fare niente.

    L'eccezione c'era, ed era il wizard riaperto da Impostazioni: li' allo step
    0 il back doveva riportare da dove si era arrivati. Se n'e' andata con la
    riapertura stessa (21/09/2026) — un ramo che non poteva piu' scattare, e che
    prometteva un'uscita a chi leggeva.
    """
    source = _onboarding()
    # Eseguito, non letto: `handleBack()` vero a ogni step, e ogni volta la
    # pressione e' consumata. Cercare `return false;` nel sorgente lasciava
    # passare un `return this.step !== 0;` — l'uscita dal wizard senza provider.
    run_js(
        "import assert from 'node:assert/strict';\n"
        "class W {\n"
        "  _goToStep0() { this.step = 0; }\n"
        "  _goBackToStep1() { this.step = 1; }\n"
        f"{member(source, 'handleBack')}\n"
        "}\n"
        """
for (const [step, saving, after] of [[0, false, 0], [1, false, 0], [2, false, 1],
                                    [3, false, 3], [2, true, 2]]) {
  const w = new W();
  w.step = step;
  w.saving = saving;
  assert.equal(w.handleBack(), true, `step ${step}: la pressione esce dal wizard`);
  assert.equal(w.step, after, `step ${step}: finito sullo step ${w.step}`);
}
"""
    )
    back = _method(source, "handleBack")
    assert "_rerun" not in back, "il ramo dell'uscita e' tornato senza la strada che lo accendeva"
    assert "return false;" not in back, (
        "una pressione non consumata esce dal wizard: al primo avvio non c'e' dove andare"
    )
    assert "return true;" in back, "la pressione va consumata sempre"

    # E la voce del dock la governa un posto solo: chi sa quando il blocco
    # comincia e quando finisce.
    deactivate = _method(source, "deactivate")
    assert "nav-onboarding" not in deactivate, (
        "il wizard rimette mano alla voce del dock: quel conto lo tiene _setFirstRunLock"
    )
    assert "_setFirstRunLock" in _app(), "nessuno accende piu' la voce del dock al primo avvio"


def test_an_unreadable_settings_call_is_not_read_as_configured() -> None:
    """Il ``catch`` del boot ingoiava tutto e proseguiva come se ``first_run``
    fosse ``false``. Ma "non lo so" non è "onboarding già fatto": il ramo
    successivo *consuma* il marcatore ``onboarding-complete``, quindi un errore
    transitorio (gateway a metà avvio, token non ancora valido) lasciava una
    Jenny senza provider e senza più alcuna strada verso il wizard."""
    body = re.search(r"\n  async init\(\)\s*\{(.*?)\n  \}", _app(), re.S)
    assert body, "init() non trovato"
    init = body.group(1)

    assert "let firstRunKnown = false;" in init
    assert "firstRunKnown = true;" in init
    assert re.search(r"if \(firstRunKnown && !this\._firstRun && localStorage\.getItem", init), (
        "il ramo 'onboarding appena concluso' gira anche quando lo stato è ignoto"
    )
    # rsplit: il primo `catch (err)` di init() è quello di api.bootstrap(),
    # l'ultimo è quello delle impostazioni — che è il ramo in discussione.
    catch = init.rsplit("} catch (err) {", 1)
    assert len(catch) == 2, "il catch del boot non distingue più il caso d'errore"
    assert "firstRunKnown = true;" not in catch[1], (
        "il catch non deve dichiarare noto ciò che non ha potuto leggere"
    )
    assert "api.clientLog(" in catch[1], (
        "la console del WebView si legge solo via adb: un boot degradato deve lasciare traccia"
    )


def test_the_strings_of_the_button_went_with_the_button() -> None:
    """Le quattro stringhe del «riesegui» non esistono piu', ed e' il punto.

    Una stringa tradotta che nessuno usa non rompe niente oggi: sopravvive
    alle riscritture e alla revisione dei testi, e la prima volta che qualcuno
    la riusa si porta dietro un copy scritto per un'altra schermata. Toglierle
    insieme al bottone e' la meta' del lavoro che si dimentica sempre.
    """
    orfane = ["rerunOnboarding", "rerunOnboardingHint",
              "rerunOnboardingAction", "rerunOnboardingConfirm"]
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        rimaste = [k for k in orfane if k in data["settings"]]
        assert not rimaste, (
            f"{locale}.json tiene ancora le stringhe di un bottone che non "
            f"c'e' piu': {rimaste}"
        )
