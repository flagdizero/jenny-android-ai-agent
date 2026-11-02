"""La configurazione guidata: quello che si digita non si perde, e da lì si esce.

Tre difetti con la stessa radice — il wizard tratta il proprio stato come se il
tempo non passasse fra un render e l'altro:

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
  personalizzato" su un form che non ce l'ha.

**Dal 27/09/2026 il wizard ha un documento suo**, ``onboarding.html``. Era un
«modo» dell'officina, con un blocco del dock che lo teneva chiuso dentro; quando
la casa è diventata il documento di partenza nessuno ci arrivava più, perché il
controllo del primo avvio stava solo nell'officina. Ora casa e officina
rimandano lì, e il blocco non esiste più perché non c'è niente da bloccare.

Asserzioni sul sorgente, nello stile di ``test_back_navigation_contract.py``,
più quel che si può eseguire davvero.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "jenny" / "templates" / "ui"
ASSETS = UI / "assets"
WIZARD_JS = ASSETS / "onboarding-wizard.js"
HOST_JS = ASSETS / "onboarding-app.js"
FIRST_RUN_JS = ASSETS / "shared" / "first-run.js"
DIALOG_JS = ASSETS / "shared" / "dialog.js"
HOME_JS = ASSETS / "home-app.js"
WORKSHOP_JS = ASSETS / "mobile-app.js"
SETTINGS_JS = ASSETS / "mobile-settings.js"
I18N = ASSETS / "i18n"

ONBOARDING_URL = "'/html-mobile/onboarding.html', { replace: true }"
HOME_URL = "'/html-mobile/', { replace: true }"


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


def _wizard() -> str:
    return WIZARD_JS.read_text(encoding="utf-8")


def _host() -> str:
    return HOST_JS.read_text(encoding="utf-8")


def _workshop() -> str:
    return WORKSHOP_JS.read_text(encoding="utf-8")


def _home() -> str:
    return HOME_JS.read_text(encoding="utf-8")


# ── #11 · i campi si catturano prima di cambiare step ────────────────────────


def test_going_back_to_step_zero_keeps_what_was_typed() -> None:
    """``_renderStep1`` ridisegna i campi *dallo stato*: ciò che non viene
    travasato prima del cambio step non esiste più. Su una tastiera fisica una
    chiave API è la cosa più scomoda da riscrivere, ed era la cosa che si
    perdeva più spesso — il tasto Indietro dell'onboarding porta proprio lì."""
    source = _wizard()
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
    back = _method(_wizard(), "handleBack")
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
    source = _wizard()
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


# ── Il primo avvio ha un documento suo ───────────────────────────────────────


@requires_node
def test_first_run_has_three_answers() -> None:
    """«Non lo so» non è né «sì» né «no». Preso per «configurato» lasciava una
    Jenny senza provider e senza strada verso il wizard; preso per «primo
    avvio» manderebbe al wizard chi ha già i suoi provider, che
    ``onboarding.save`` sostituirebbe.

    La lettura della casa (``_askSettings``) un fallimento non lo lancia: lo
    rende come ``null``. Anche quello è «non lo so» — era proprio la forma in
    cui un errore sarebbe passato per «no»."""
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"import {{ isFirstRun }} from '{FIRST_RUN_JS.as_uri()}';\n"
        """
assert.equal(await isFirstRun(async () => ({ first_run: true })), true);
assert.equal(await isFirstRun(async () => ({ first_run: false })), false);
assert.equal(await isFirstRun(async () => { throw new Error('401'); }), null);
assert.equal(await isFirstRun(async () => null), null, 'una lettura inghiottita e resa null');
assert.equal(await isFirstRun(async () => undefined), null);
"""
    )


def test_home_and_workshop_send_a_first_run_to_the_onboarding() -> None:
    """Il difetto: la casa, documento di partenza, non guardava ``first_run``;
    lo guardava solo l'officina. Un'installazione pulita atterrava in una casa
    senza nessuno a rispondere, e il wizard non compariva mai (misurato
    sull'emulatore il 26/09/2026).

    Tutte e due chiedono la stessa cosa allo stesso modulo, e **prima** di
    aprire il filo o leggere la storia. `replace`: Indietro dal wizard non deve
    tornare a un guscio senza provider."""
    home = _home()
    redirect = _method(home, "_goToOnboardingIfFirstRun")
    assert "isFirstRun(" in redirect
    assert f"api.navigate({ONBOARDING_URL})" in redirect
    assert "if (first === null)" in redirect, "un «non lo so» non deve mandare al wizard"

    init = member(home, "init", prefixes=("async ",))
    check = init.index("await this._goToOnboardingIfFirstRun()")
    assert init.index("await api.bootstrap()") < check, "senza credenziale la lettura fallisce"
    for later in ("wsManager.connectChat()", "await i18n.load(", "this._readThread("):
        assert check < init.index(later), f"la casa fa {later} prima di sapere se e' il primo avvio"

    workshop = _workshop()
    winit = member(workshop, "init", prefixes=("async ",))
    assert "isFirstRun(" in winit
    assert f"api.navigate({ONBOARDING_URL})" in winit
    assert winit.index("await api.bootstrap()") < winit.index("isFirstRun(")
    assert winit.index("isFirstRun(") < winit.index("await this._initSessions(")


def test_a_home_that_could_not_ask_asks_again_when_the_wire_opens() -> None:
    """Al boot il gateway può essere a metà avvio: la lettura fallisce e la casa
    resta. Il socket che si apre dice che adesso c'è, e si richiede — una volta
    sola, perché un secondo «non lo so» non deve far girare niente in tondo."""
    home = _home()
    wire = _method(home, "_onWireOpen")
    assert "if (this._firstRunUnknown)" in wire
    assert "this._firstRunUnknown = false;" in wire
    assert "retry: false" in wire, "il secondo tentativo riaccenderebbe il terzo"
    assert "this._firstRunUnknown = retry;" in _method(home, "_goToOnboardingIfFirstRun")


def test_the_onboarding_goes_home_only_on_a_certain_no() -> None:
    """Il documento dell'onboarding fa la domanda al contrario: chi ha già i
    suoi provider torna alla casa. Ma **solo su un no certo**: la casa ha
    appena visto il primo avvio, e rimandarla indietro su una lettura fallita
    farebbe rimbalzare i due documenti fra loro."""
    init = _method(_host(), "init")
    assert "=== false" in init
    assert f"api.navigate({HOME_URL})" in init
    assert init.index("=== false") < init.index("new OnboardingController()")


def test_finishing_the_wizard_lands_in_the_home() -> None:
    """Il «Fatto» porta alla casa, su Jenny, dove il saluto scritto da
    ``onboarding.save`` aspetta. Prima ricaricava la pagina, cioè l'officina.

    E niente marcatori: ``onboarding-complete`` esisteva per sbloccare il dock
    dell'officina, e ``_setFirstRunLock`` era quel blocco. Nessuno dei due ha
    più una ragione, e nel documento nuovo il secondo sarebbe un TypeError."""
    source = _wizard()
    complete = _method(source, "_complete")
    assert f"api.navigate({HOME_URL})" in complete
    assert "api.reload()" not in complete, "ricaricherebbe il wizard, non la casa"
    for gone in ("_setFirstRunLock", "onboarding-complete", "mobile-last-mode"):
        assert gone not in source, f"il wizard tocca ancora {gone}, che era dell'officina"


def test_the_workshop_no_longer_hosts_the_wizard() -> None:
    """Il blocco del primo avvio era cucito in sette punti dell'officina (dock,
    swipe, launcher, ``goHome``, ``openChat``, il boot, la mascotte). Esce
    tutto: una seconda strada verso lo stesso wizard divergerebbe dalla prima
    alla prima correzione."""
    workshop = _workshop()
    for gone in ("OnboardingController", "_setFirstRunLock", "this._firstRun",
                 "'nav-disabled'", "whenShellReady(", "_shellReadyCbs"):
        assert gone not in workshop, f"{gone} e' tornato nell'officina"
    html = (UI / "workshop.html").read_text(encoding="utf-8")
    assert 'id="view-onboarding"' not in html
    assert 'id="nav-onboarding"' not in html
    # Un `mobile-last-mode` salvato a meta' wizard non e' piu' una vista.
    assert "if (initialMode === 'onboarding') initialMode = 'chat';" in workshop
    assert "removeStorage('onboarding-complete');" in workshop


def test_the_host_answers_the_whole_native_contract() -> None:
    """``MainActivity`` chiama ``goHome()`` senza guardare se esiste: un metodo
    mancante è un TypeError dentro la sua ``evaluateJavascript``. I sei metodi
    del contratto ci sono tutti, anche se qui quasi nessuno fa qualcosa."""
    host = _host()
    for name in ("onNativeReady", "goHome", "onPackageChanged", "handleHardwareBack",
                 "openChat", "isChatOnScreen"):
        member(host, name)
    assert "window.mobileApp = this;" in host
    assert "return true;" in _method(host, "openChat"), (
        "un false farebbe ricaricare il guscio in cerca di una chat che non c'e'"
    )


@requires_node
def test_the_mini_jenny_falls_even_when_the_native_ready_already_came() -> None:
    """``onNativeReady`` arriva una volta per WebView, al primo
    ``onPageFinished`` (``MainActivity``, ``if (loaded) return``). Dalla casa
    all'onboarding quel momento è già passato: senza il timer la caduta della
    mini Jenny aspetterebbe per sempre."""
    host = _host()
    run_js(
        "import assert from 'node:assert/strict';\n"
        "globalThis.window = { JennyNative: {} };\n"
        "const SHELL_READY_FALLBACK_MS = 20;\n"
        "class H {\n"
        "  constructor() { this._shellReady = false; this._shellReadyCbs = []; }\n"
        f"{member(host, 'onNativeReady')}\n"
        f"{member(host, 'whenShellReady')}\n"
        "}\n"
        """
const h = new H();
let ran = 0;
h.whenShellReady(() => { ran++; });
assert.equal(ran, 0);
await new Promise((r) => setTimeout(r, 60));
assert.equal(ran, 1, 'senza onNativeReady la caduta non parte mai');
h.onNativeReady();
assert.equal(ran, 1, 'una seconda chiamata la rifarebbe partire');
"""
    )


# ── N25 · una strada sola verso il wizard ────────────────────────────────────


def test_the_wizard_is_only_the_first_run() -> None:
    """«Riesegui la configurazione» non c'e' piu', ed e' una decisione.

    `save_onboarding` fa `config.providers.providers = [one]`: **sostituisce**
    l'elenco invece di aggiungere. In una schermata da operatore quel bottone
    puo' solo toglierti marche che hai configurato — e tutto cio' che il wizard
    imposta si fa meglio altrove.

    L'unico modo di arrivare al wizard e' il primo avvio: casa e officina ci
    rimandano solo su ``first_run`` vero, e nessun altro punto nomina il
    documento.
    """
    settings = SETTINGS_JS.read_text(encoding="utf-8")
    assert "btn-rerun-onboarding" not in settings, (
        "il bottone e' tornato: in officina puo' solo togliere marche"
    )
    assert "_rerunOnboarding" not in settings
    assert "markRerun" not in _wizard(), (
        "il wizard sa di nuovo di «essere rifatto», ma nessuno puo' rifarlo"
    )

    doors = sorted(
        path.name
        for path in [*ASSETS.glob("*.js"), *(ASSETS / "shared").glob("*.js")]
        if f"api.navigate({ONBOARDING_URL})" in path.read_text(encoding="utf-8")
    )
    assert doors == ["home-app.js", "mobile-app.js"], (
        f"una strada in piu' verso il wizard: {doors}"
    )


@requires_node
def test_from_the_first_run_there_is_no_way_out() -> None:
    """Dal wizard del primo avvio non si esce col back, senza eccezioni: sotto
    non c'e' niente, e una Jenny senza provider portata in chat non puo' fare
    niente. L'host consegna il tasto al wizard, che consuma sempre."""
    source = _wizard()
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
    assert "return false;" not in back, (
        "una pressione non consumata esce dal wizard: al primo avvio non c'e' dove andare"
    )
    assert "this.controller.handleBack()" in _method(_host(), "handleHardwareBack")


def test_the_strings_of_the_button_went_with_the_button() -> None:
    """Le stringhe del «riesegui», e la voce «Setup» del dock dell'officina,
    non esistono piu', ed e' il punto.

    Una stringa tradotta che nessuno usa non rompe niente oggi: sopravvive
    alle riscritture e alla revisione dei testi, e la prima volta che qualcuno
    la riusa si porta dietro un copy scritto per un'altra schermata. Toglierle
    insieme al bottone e' la meta' del lavoro che si dimentica sempre.
    """
    orphans = ["rerunOnboarding", "rerunOnboardingHint",
              "rerunOnboardingAction", "rerunOnboardingConfirm"]
    for locale in ("it", "en"):
        data = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))
        remaining = [k for k in orphans if k in data["settings"]]
        assert not remaining, (
            f"{locale}.json tiene ancora le stringhe di un bottone che non "
            f"c'e' piu': {remaining}"
        )
        assert "onboarding" not in data["nav"], f"{locale}.json: la voce del dock e' uscita"


# ── Dal collaudo del 27/09/2026 ──────────────────────────────────────────────


def _render_step1(fields: str) -> str:
    """L'HTML vero di ``_renderStep1`` con lo stato *fields*, su un contenitore
    finto che accetta i listener."""
    source = _wizard()
    shared = (ASSETS / "shared" / "api-base.js").read_text(encoding="utf-8")
    consts = "\n".join(
        re.search(rf"^const {name} = .*?;$", source, re.S | re.M).group(0)
        for name in ("DEFAULT_API_BASE", "PLACEHOLDER_SUFFIX")
    ) + "\n" + re.search(r"^export (const NO_AUTOCORRECT = .*?;)$", shared, re.M).group(1)
    return run_js(
        "const i18n = { t: (k) => k };\n"
        "const escapeHtml = (s) => String(s);\n"
        f"{consts}\n"
        "class W {\n"
        "  _progress() { return ''; }\n"
        f"{member(source, '_renderStep1')}\n"
        "}\n"
        "const w = new W();\n"
        "w.contentEl = { innerHTML: '', querySelector: () => ({ addEventListener() {} }) };\n"
        f"Object.assign(w, {fields});\n"
        "w._renderStep1();\n"
        "process.stdout.write(w.contentEl.innerHTML);\n"
    )


@requires_node
def test_next_is_already_on_when_name_and_key_come_back() -> None:
    """Il grave del collaudo: passo 1 → Next → Back (o 1 → Back → Next) e Next
    restava spento con i campi pieni, perché il tasto nasceva ``disabled``
    fisso e si riaccendeva solo scrivendo. Lo stato viene dai campi."""
    full = _render_step1("{ format: 'openai_compat', providerName: 'p', apiKey: 'k', apiBase: '' }")
    assert re.search(r'id="btn-next-1"\s*>', full), "con nome e chiave Next nasce spento"
    for fields in ("{ format: 'openai_compat', providerName: 'p', apiKey: '', apiBase: '' }",
                   "{ format: 'openai_compat', providerName: '', apiKey: 'k', apiBase: '' }"):
        assert re.search(r'id="btn-next-1"\s*disabled>', _render_step1(fields)), fields


@requires_node
def test_the_examples_follow_the_chosen_format() -> None:
    """Scegliendo OpenAI il nome d'esempio era «My Claude» e la chiave
    ``sk-ant-api03-...``, scritta nel codice. E i campi tecnici tengono lontana
    l'autocorrezione, che su «http://» scriveva «Http:/»."""
    openai = _render_step1("{ format: 'openai_compat', providerName: '', apiKey: '', apiBase: '' }")
    anthropic = _render_step1("{ format: 'anthropic', providerName: '', apiKey: '', apiBase: '' }")
    assert "onboarding.providerNamePlaceholderOpenai" in openai
    assert "onboarding.apiKeyPlaceholderOpenai" in openai
    assert "onboarding.providerNamePlaceholderAnthropic" in anthropic
    assert "onboarding.apiKeyPlaceholderAnthropic" in anthropic
    assert "sk-ant" not in _wizard(), "una chiave d'esempio scritta nel codice invece che in i18n"

    base = re.search(r'<input[^>]*id="api-base"[^>]*>', openai, re.S).group(0)
    for attr in ('type="url"', 'inputmode="url"', 'autocorrect="off"',
                 'autocapitalize="none"', 'spellcheck="false"'):
        assert attr in base, f"Base URL senza {attr}"

    for locale in ("it", "en"):
        onboarding = json.loads((I18N / f"{locale}.json").read_text(encoding="utf-8"))["onboarding"]
        assert "GPT" not in onboarding["providerNameHint"], "l'aiuto del nome cita ancora una marca"


@requires_node
def test_a_model_of_another_provider_does_not_survive() -> None:
    """Trovato rileggendo il codice: scelto un modello, tornare indietro e
    cambiare chiave o formato lasciava Launch acceso col modello dell'altro
    provider. Lo stesso provider invece lo tiene."""
    source = _wizard()
    run_js(
        "import assert from 'node:assert/strict';\n"
        f"const {{ normalizeApiBase }} = await import('{(ASSETS / 'shared' / 'api-base.js').as_uri()}');\n"
        "const toasts = [];\n"
        "const showToast = (m) => toasts.push(m);\n"
        "const i18n = { t: (k) => k };\n"
        "class W {\n"
        "  _captureStep1() {}\n"
        "  _loadModels() {}\n"
        f"{member(source, '_modelsFingerprint')}\n"
        f"{member(source, '_goToStep2')}\n"
        "}\n"
        """
const w = new W();
Object.assign(w, { format: 'openai_compat', apiKey: 'a', apiBase: '', model: '', _modelsFor: null });
w._goToStep2();
w.model = 'gpt-x';
w._goToStep2();
assert.equal(w.model, 'gpt-x', 'stesso provider: il modello si tiene');
w.apiKey = 'b';
w._goToStep2();
assert.equal(w.model, '', 'chiave cambiata: il modello era di un altro provider');
w.model = 'gpt-y';
w.format = 'anthropic';
w._goToStep2();
assert.equal(w.model, '', 'formato cambiato: il modello era di un altro provider');

// L'indirizzo storpiato dall'autocorrezione si ferma qui, e «Http://» si
// corregge da solo (collaudo del 27/09/2026).
w.step = 1;
w.apiBase = 'Http:/10.0.2.2:8765/v1';
w._goToStep2();
assert.equal(w.step, 1, 'con un indirizzo storpiato si e andati a chiedere i modelli');
assert.deepEqual(toasts, ['onboarding.baseUrlInvalid']);
w.apiBase = 'Http://10.0.2.2:8765/v1';
w._goToStep2();
assert.equal(w.step, 2);
assert.equal(w.apiBase, 'http://10.0.2.2:8765/v1');
"""
    )
    assert "this.model = ''" in _method(source, "_selectFormat")


@requires_node
def test_back_closes_the_restore_passphrase_before_the_step() -> None:
    """``ead21dad`` aveva insegnato a casa e officina a chiudere il dialog della
    passphrase col Back; l'onboarding, che ha il suo host, cambiava invece il
    passo sotto al dialog. Il «Restart now» rifiuta il ``cancel`` e resta."""
    run_js(
        "import assert from 'node:assert/strict';\n"
        "function dialog(refuse) {\n"
        "  return { open: true, dispatchEvent(e) { return !(refuse && e.cancelable); },\n"
        "           close() { this.open = false; } };\n"
        "}\n"
        "let open = [];\n"
        "globalThis.document = { querySelectorAll: () => open.filter((d) => d.open) };\n"
        f"{function(DIALOG_JS.read_text(encoding='utf-8'), 'dismissTopDialog')}\n"
        "class H {\n"
        f"{member(_host(), 'handleHardwareBack')}\n"
        "}\n"
        """
let steps = 0;
const h = new H();
h.controller = { handleBack() { steps++; return true; } };
const passphrase = dialog(false);
open = [passphrase];
assert.equal(h.handleHardwareBack(), true);
assert.equal(passphrase.open, false, 'la passphrase resta aperta');
assert.equal(steps, 0, 'il passo sotto al dialog e cambiato');
const restart = dialog(true);
open = [restart];
assert.equal(h.handleHardwareBack(), true);
assert.equal(restart.open, true, 'il Restart now si e chiuso');
assert.equal(steps, 0);
open = [];
h.handleHardwareBack();
assert.equal(steps, 1, 'senza dialog il Back non risale piu');
"""
    )


def test_every_wizard_icon_exists_and_anthropic_is_not_figma() -> None:
    """La scheda Anthropic mostrava il logo di Figma (``ti-brand-figma``):
    Tabler un'icona Anthropic non ce l'ha, e ora c'è il segno vero in SVG."""
    from test_command_specs import _tabler_icon_names

    available = _tabler_icon_names()
    used = set(re.findall(r"\bti-([a-z0-9-]+)", _wizard()))
    assert used and used <= available, f"icone che il font non ha: {sorted(used - available)}"
    assert "brand-figma" not in used
    brand = (ASSETS / "shared" / "provider-brand.js").read_text(encoding="utf-8")
    logo = re.search(r"const ANTHROPIC_LOGO = (.*?);\n", brand, re.S).group(1)
    assert 'aria-hidden="true"' in logo and 'fill="currentColor"' in logo
    assert "http" not in logo, "il logo deve stare in linea: la CSP non carica niente da fuori"
