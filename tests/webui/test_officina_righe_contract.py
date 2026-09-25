"""Le righe dell'officina: nome a sinistra, comando a destra.

La tavola mette etichetta e controllo **sulla stessa riga**, alta 44 px, con il
comando di larghezza fissa. L'app li impilava: etichetta sopra, campo a tutta
larghezza sotto. E' la differenza che si ripeteva piu' volte nei tre cassetti —
«Parametri», «Ricerca web», i campi di Dream e del giardiniere.

**E c'e' una trappola, che questo banco esiste soprattutto per tenere chiusa.**
Le classi `.settings-field` / `.settings-label` / `.settings-input` non sono solo
dell'officina: le usano anche ``shared/backup-flow.js`` e
``shared/telegram-pairing.js``, e backup-flow disegna i **due campi della
passphrase dentro una finestra, anche in casa**. Allineare l'officina cambiando
`.settings-field` avrebbe schiacciato una password in 92 px, in una schermata
che con l'officina non c'entra niente.

Per questo la riga a due colonne e' una **classe nuova**, `.settings-row`, e i
banchi qui sotto chiedono che resti tale.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from support.js_harness import function, member, requires_node, run_js

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"

SETTINGS = (ASSETS / "mobile-settings.js").read_text(encoding="utf-8")
CSS = (ASSETS / "mobile-style.css").read_text(encoding="utf-8")
BACKUP = (ASSETS / "shared" / "backup-flow.js").read_text(encoding="utf-8")


def _body(name: str) -> str:
    """Il corpo di un metodo di `SettingsController`, per nome.

    `async` è opzionale: senza, i tre metodi che aspettano la rete —
    proprio quelli che questi banchi devono leggere — risultavano "non
    trovati", che è un falso verde travestito da rosso.
    """
    m = re.search(rf"\n  (?:async )?{name}\((.*?)\n  \}}", SETTINGS, re.S)
    assert m, f"{name} non trovato"
    return m.group(1)


# ── La riga a due colonne ────────────────────────────────────────────────────


@pytest.mark.parametrize("helper", ("_field", "_select", "_numberField"))
def test_i_tre_aiutanti_fanno_una_riga_non_una_pila(helper: str) -> None:
    """I tre che disegnano «etichetta + controllo» usano la riga, non la pila."""
    body = _body(helper)
    assert 'class="settings-row"' in body, (
        f"{helper} disegna ancora una pila: l'etichetta finirebbe sopra il campo"
    )
    assert 'class="settings-field"' not in body


def test_la_riga_ha_le_sue_regole() -> None:
    """Senza queste, `.settings-row` eredita il nulla e la riga non esiste."""
    m = re.search(r"^\.settings-row \{(.*?)\}", CSS, re.S | re.M)
    assert m, ".settings-row non ha una regola sua"
    regola = m.group(1)
    assert "flex-direction: row" in regola, "la riga non e' orizzontale"
    assert re.search(r"min-height:\s*44px", regola), (
        "la riga non arriva a 44px: sotto quella misura il dito manca il bersaglio"
    )


def test_i_comandi_hanno_una_larghezza_fissa() -> None:
    """Un numero e un menu' a tutta larghezza sono la pila di prima con un altro
    nome: e' la larghezza fissa a fare le due colonne."""
    for selettore, largh in (
        (r"\.settings-row > \.settings-input", "92px"),
        (r"\.settings-row > \.settings-select", "150px"),
    ):
        m = re.search(rf"{selettore} \{{([^}}]*)\}}", CSS)
        assert m, f"{selettore} senza regola"
        assert f"width: {largh}" in m.group(1), f"{selettore}: larghezza non fissa"


def test_i_numeri_sono_allineati_a_destra() -> None:
    """Incolonnati, tre numeri si confrontano con l'occhio."""
    m = re.search(r"\.settings-row > \.settings-input \{([^}]*)\}", CSS)
    assert m and "text-align: right" in m.group(1)


# ── La trappola: la casa non si storce ───────────────────────────────────────


def test_la_pila_resta_per_chi_la_usa_davvero() -> None:
    """`.settings-field` resta una **colonna**.

    E' la classe dei due campi della passphrase, che vivono in una finestra e
    anche in casa. Se qualcuno la rende orizzontale per allineare l'officina,
    quella password diventa larga 92 px in una schermata che non c'entra.
    """
    m = re.search(r"^\.settings-field \{(.*?)\}", CSS, re.S | re.M)
    assert m, ".settings-field non ha piu' una regola"
    assert "flex-direction: column" in m.group(1), (
        ".settings-field non e' piu' una pila: guarda i campi della passphrase "
        "in shared/backup-flow.js prima di dire che va bene"
    )


def test_il_flusso_condiviso_usa_ancora_la_pila() -> None:
    """La prova che la trappola e' reale e non teorica: il file c'e', e la usa."""
    assert 'class="settings-field"' in BACKUP
    assert 'type="password"' in BACKUP, (
        "se la passphrase non e' piu' un campo password, questo banco va riletto"
    )


# ── I valori di macchina in monospazio ───────────────────────────────────────


@pytest.mark.parametrize(
    "selettore",
    (".model-inuse-name", ".provider-name"),
)
def test_i_valori_di_macchina_sono_in_monospazio(selettore: str) -> None:
    """Un nome di modello, un endpoint e una chiave sono identificatori.

    In proporzionale si leggono come parole e si confrontano male con quelli
    scritti altrove (in un file di config, in un messaggio d'errore). Il token
    esiste in tutti e sette i temi.
    """
    m = re.search(re.escape(selettore) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"{selettore} senza regola"
    assert "var(--font-mono)" in m.group(1), f"{selettore} non e' in monospazio"


def test_il_monospazio_viene_dal_token_e_non_da_un_nome_di_carattere() -> None:
    """Sette temi, sette caratteri possibili: inchiodarne uno ne rompe sei."""
    for row in CSS.splitlines():
        if "font-family" in row and ("Fira Code" in row or "monospace" in row):
            assert "--font-mono" in row or "@font-face" in row or "--font-" in row, (
                f"carattere monospazio scritto a mano invece che dal token: {row.strip()}"
            )


# ── Riassumere invece di elencare ────────────────────────────────────────────
#
# La differenza più grossa fra i cassetti e le tavole non era di stile: era di
# **quanto c'è a schermo**. Memoria misurava 6 467 px sul telefono (foto intera,
# 21/09/2026) e quasi due terzi erano l'elenco delle istantanee, una riga per
# ognuna. Nessuna di quelle righe risponde alla domanda per cui si apre il
# gruppo — «ce l'ho una storia, e quanto va indietro?» — a cui invece bastano
# due numeri.
#
# Il criterio della tavola, che questi banchi tengono fermo: **in cassetto quel
# che si legge, l'amministrazione dietro un tocco.**

WORKSHOP_HTML = (ROOT / "jenny" / "templates" / "ui" / "workshop.html").read_text(encoding="utf-8")


def test_la_storia_in_cassetto_e_una_riga() -> None:
    """`_renderBackup` non disegna più l'elenco, il menù, né «crea adesso»."""
    body = _body("_renderBackup")
    assert "_summary(" in body, "la storia non è più riassunta in una riga"
    for roba in ("snapshot-list", "snapshot-retention", "btn-snapshot-create"):
        assert roba not in body, f"«{roba}» è tornato disteso nel cassetto"


def test_il_dettaglio_vive_nel_pannello() -> None:
    """Niente è stato **tolto**: è solo andato dietro il tocco."""
    body = _body("_openHistory")
    for roba in ("snapshot-list", "snapshot-retention", "btn-snapshot-create"):
        assert roba in body, f"«{roba}» non è nel pannello: allora è sparito davvero"
    assert 'id="drawer-history"' in WORKSHOP_HTML, "il pannello non esiste nel documento"
    assert 'id="drawer-history-body"' in WORKSHOP_HTML


def test_il_corpo_del_pannello_si_disegna_all_apertura() -> None:
    """Un pannello chiuso **non ha i suoi nodi**.

    Se il corpo si disegnasse al caricamento della schermata, `_loadSnapshotList`
    scriverebbe nel vuoto e la riga resterebbe su «Caricamento…» — in silenzio,
    che è il modo in cui questo difetto è già arrivato sul telefono una volta.
    """
    assert "this._openHistory" in SETTINGS, "niente collega la riga al suo pannello"
    open = _body("_openHistory")
    assert "_wireHistory" in open and "_loadSnapshotList" in open, (
        "il pannello si disegna ma non si aggancia né si riempie"
    )
    # E il caricatore cerca il nodo nel documento, non dentro la vista: il
    # pannello vive fuori da `contentEl`.
    carico = _body("_loadSnapshotList")
    assert "document.getElementById('snapshot-list')" in carico
    assert "contentEl.querySelector('#snapshot-list')" not in carico


@requires_node
def test_il_riepilogo_racconta_la_piu_vecchia_non_la_piu_recente() -> None:
    """Quanto **indietro** si può tornare: è la cosa per cui una storia esiste.

    La più recente è quasi sempre «poco fa» e non distingue una storia di venti
    istantanee da una di due.
    """
    # Eseguito, non cercato: `Math.min(` nel sorgente lasciava passare un
    # riepilogo che guardava la prima della lista — che il server manda dalla
    # più recente — o un `Math.min` su un solo elemento.
    out = run_js(
        "import assert from 'node:assert/strict';\n"
        "const i18n = { t: (k, p) => k + ' ' + JSON.stringify(p || {}) };\n"
        "const whenText = (ms) => 'ms=' + ms;\n"
        "let reply;\n"
        "const api = { getSnapshotHistory: async () => reply };\n"
        "class C {\n"
        "  constructor() { this._gen = 0; this.el = { textContent: '' };\n"
        "    this.contentEl = { querySelector: () => this.el }; }\n"
        "  _stale(g) { return g !== this._gen; }\n"
        f"{member(SETTINGS, '_loadHistorySummary')}\n"
        "}\n"
        """
const c = new C();
reply = { snapshots: [
  { created_at_ms: 3000 }, { created_at_ms: 1000 }, { created_at_ms: 2000 },
] };
await c._loadHistorySummary();
assert.equal(c.el.textContent,
  'backup.snapshotSummary ' + JSON.stringify({ count: 3, when: 'ms=1000' }));
reply = { snapshots: [] };
await c._loadHistorySummary();
assert.match(c.el.textContent, /^backup.snapshotSummaryEmpty/);
console.log('ok');
"""
    )
    assert out.strip() == "ok"


def test_il_riepilogo_non_resta_a_caricamento_per_sempre() -> None:
    """Un errore è un'informazione; un «Caricamento…» eterno è un guasto
    travestito da attesa."""
    body = _body("_loadHistorySummary")
    assert "catch" in body
    assert "snapshotHistoryUnavailable" in body


@pytest.mark.parametrize("lingua", ("it", "en"))
def test_le_due_frasi_del_riepilogo_esistono(lingua: str) -> None:
    import json

    d = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))["backup"]
    assert "{count}" in d["snapshotSummary"] and "{when}" in d["snapshotSummary"], (
        f"{lingua}: il riepilogo non porta i due numeri che lo rendono utile"
    )
    assert d["snapshotSummaryEmpty"].strip()


def test_le_righe_di_riepilogo_si_agganciano_con_una_regola_sola() -> None:
    """Ne arriveranno altre due (Telegram, SSH): un `if` per ognuna le farebbe
    divergere una per volta."""
    assert "[data-summary]" in SETTINGS, "il cablaggio non è generico"
    assert "_OPEN_PANEL" in SETTINGS, "manca la tabella pannello -> chi lo riempie"


def test_telegram_in_cassetto_e_una_riga() -> None:
    """Il widget di accoppiamento è amministrazione: token, codice, disaccoppia.

    In cassetto ne resta la risposta alla sola domanda che si fa da lì: «posso
    scriverle da fuori, adesso?».
    """
    body = _body("_renderTelegram")
    assert "_summary(" in body
    assert "settings-telegram-widget" not in SETTINGS, (
        "il widget è ancora montato nel cassetto invece che nel pannello"
    )
    assert 'id="drawer-telegram-body"' in WORKSHOP_HTML


def test_il_widget_si_monta_all_apertura_del_pannello() -> None:
    body = _body("_openTelegram")
    assert "TelegramPairingWidget" in body and "drawer-telegram-body" in body
    assert "destroy()" in body, (
        "riaprire il pannello lascerebbe due widget vivi sullo stesso stato"
    )


def test_la_riga_telegram_si_legge_senza_il_widget() -> None:
    """La riga deve dire qualcosa **prima** che il pannello esista."""
    body = _body("_loadTelegramSummary")
    assert "getTelegramStatus" in body, "la riga aspetta il widget per sapere cosa dire"
    assert "_writeTelegramSummary(" in body
    assert "telegramSummary" in _body("_writeTelegramSummary")


@requires_node
def test_la_riga_telegram_segue_il_widget() -> None:
    """Accoppiato dal pannello, la riga in cassetto restava su «non collegato»:
    si leggeva una volta sola, al disegno. Ora ogni stato che il widget disegna
    arriva anche alla riga. Widget e pannello veri, in node."""
    tg = (ASSETS / "shared" / "telegram-pairing.js").read_text(encoding="utf-8")
    out = run_js(
        "import assert from 'node:assert/strict';\n"
        "const i18n = { t: (k, p) => k + (p ? ' ' + JSON.stringify(p) : '') };\n"
        f"{function(tg, 'telegramSummary')}\n"
        "class TelegramPairingWidget {\n"
        f"{member(tg, 'constructor')}\n"
        f"{member(tg, 'render')}\n"
        "  _stopPolling() {} _startPolling() {}\n"
        "  _renderDisabled() {} _renderPaired() {} _renderPairing() {} _renderTokenForm() {}\n"
        "}\n"
        "const row = { textContent: 'settings.telegram.summaryNotPaired' };\n"
        "globalThis.document = { getElementById: () => ({}) };\n"
        "class C {\n"
        "  constructor() { this.contentEl = { querySelector: () => row }; }\n"
        f"{member(SETTINGS, '_openTelegram')}\n"
        f"{member(SETTINGS, '_writeTelegramSummary')}\n"
        "}\n"
        """
const c = new C();
TelegramPairingWidget.prototype.refresh = function () {};
c._openTelegram();
c._tgWidget.status = { enabled: true, configured: true, paired: true, paired_username: 'io' };
c._tgWidget.render();
assert.equal(row.textContent, 'settings.telegram.summaryPaired {"who":"@io"}');
console.log('ok');
"""
    )
    assert out.strip() == "ok"


def test_il_riassunto_telegram_copre_tutti_gli_stati() -> None:
    """Quattro stati più «non leggibile»: se ne manca uno, la riga resta vuota
    proprio nel caso che l'utente vuole capire."""
    import json

    pairing = (ASSETS / "shared" / "telegram-pairing.js").read_text(encoding="utf-8")
    assert "export function telegramSummary" in pairing
    for lingua in ("it", "en"):
        tg = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
        tg = tg["settings"]["telegram"]
        for k in ("summaryPaired", "summaryNotPaired", "summaryNoToken", "summaryOff",
                  "summaryUnknown"):
            assert tg.get(k, "").strip(), f"{lingua}: manca {k}"
        assert "{who}" in tg["summaryPaired"]


# ── SSH: la forma della tavola, senza perdere i due stati ────────────────────
#
# Qui la tavola e il codice erano in conflitto, ed è stato deciso di seguire
# **tutte e due**: la riga compatta della tavola, ma con dentro i due stati che
# il commento di `_renderSshHost` difendeva da prima —
#
#   «credenziale pronta e impronta accettata sono i due passi che l'utente deve
#   fare, e nasconderli dietro un tap lascerebbe host mezzi configurati che
#   falliscono solo al primo comando»
#
# — perché quella non era un'opinione grafica: era una misura.


def test_un_host_e_una_riga() -> None:
    body = _body("_renderSshHost")
    assert 'class="ssh-row"' in body, "l'host è tornato una scheda"
    assert "provider-card" not in body
    m = re.search(r"^\.ssh-row \{(.*?)\}", CSS, re.S | re.M)
    assert m and re.search(r"min-height:\s*52px", m.group(1)), (
        "la riga non ha l'altezza della tavola"
    )


def test_i_due_stati_restano_in_chiaro_nella_riga() -> None:
    """Il punto della decisione: **non** dietro il tocco.

    Se qualcuno li sposta nel pannello per accorciare la riga, un host a cui
    manca la chiave sembra a posto finché non fallisce il primo comando.
    """
    body = _body("_renderSshHost")
    assert body.count("this._sshMark(") == 2, "i due stati non sono più due"
    assert "has_key" in body and "pinned" in body, (
        "la riga non guarda più credenziale e impronta"
    )
    panel = _body("_openSshHost")
    assert "_sshMark" not in panel, "i due stati sono migrati dietro il tocco"


def test_lo_stato_si_distingue_anche_senza_colore() -> None:
    """Pieno contro vuoto, non solo verde contro giallo: su un tema in cui
    l'accento **è** il colore del testo, due pallini colorati si somigliano."""
    body = _body("_sshMark")
    assert "ti-circle-check-filled" in body and "ti-circle" in body, (
        "la differenza è affidata al solo colore"
    )
    assert "title=" in body, "il testo lungo non è più raggiungibile da nessuna parte"


def test_i_comandi_dell_host_stanno_nel_pannello() -> None:
    """Genera, verifica, modifica, elimina, copia: sono cose che si fanno **a**
    un host, non informazioni su di lui."""
    panel = _body("_openSshHost")
    for cmd in ("ssh-generate", "ssh-verify", "ssh-edit", "ssh-delete"):
        assert cmd in panel, f"«{cmd}» non è nel pannello"
    row = _body("_renderSshHost")
    for cmd in ("ssh-generate", "ssh-verify", "ssh-edit", "ssh-delete"):
        assert cmd not in row, f"«{cmd}» è rimasto nella riga"
    assert 'id="drawer-ssh-host"' in WORKSHOP_HTML


def test_il_cablaggio_dei_comandi_guarda_dentro_il_pannello() -> None:
    """Cercarli in `contentEl` non troverebbe niente: il pannello è fuori."""
    body = _body("_wireHostSsh")
    assert "#drawer-ssh-host-body" in body
    assert "contentEl" not in body


@pytest.mark.parametrize("lingua", ("it", "en"))
def test_le_parole_corte_dei_due_stati_esistono(lingua: str) -> None:
    import json

    ssh = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
    ssh = ssh["settings"]["ssh"]
    for k in ("markKey", "markPassword", "markFingerprint"):
        assert ssh.get(k, "").strip(), f"{lingua}: manca {k}"
        assert len(ssh[k]) <= 12, f"{lingua}: «{ssh[k]}» è troppo lungo per una riga da 52px"


# ── Secondo giro: struttura ──────────────────────────────────────────────────


def test_una_scheda_contiene_invece_di_mandare_altrove() -> None:
    """Le porte stavano in cima al cassetto, tutte insieme e staccate dal loro
    argomento: si apriva Memoria e la prima cosa erano «Workspace» e «Wiki»,
    prima ancora di sapere di cosa parlasse la pagina. Nelle tavole di Mani e
    Memoria non c'e' niente prima del primo gruppo.

    Poi ne resto' una sola — il gestore file, in fondo alla scheda «I file
    veri» — e una mappa `group -> porte` con tabelle di icone ed etichette era
    piu' codice della cosa che reggeva.

    **E il 21/09/2026 e' finita anche quella**, perche' la scheda che la
    ospitava era il difetto: otto righe di riassunto che non si toccano, e
    sotto un bottone verso l'elenco vero. Due gesti per una cosa sola, e il
    primo non rispondeva a niente. Adesso la scheda **contiene** il gestore, e
    quel che questo banco misura e' che nessuno rimetta un rimando al posto di
    un contenuto.
    """
    for morto in ("_renderPorte(", "_portePerGruppo(", "PORTE_ICONE", "PORTE_ETICHETTE"):
        assert morto not in SETTINGS, f"{morto} e' tornato: il meccanismo generico si e' rifatto"
    assert "porte: {" not in SETTINGS, "la mappa delle porte e' tornata dentro CASSETTI"
    assert "data-porta" not in SETTINGS, "la scheda e' tornata a mandare altrove invece di contenere"

    # E la scheda dei file monta il gestore vero, non una seconda copia
    # dell'elenco: due elenchi degli stessi file col tempo si raccontano
    # diversi, ed e' come il riassunto aveva cominciato.
    body = _body("_renderFile")
    assert "data-ws-grid" in body, "la scheda non ha piu' dove montare il gestore file"
    assert "listWorkspace" not in body, "la scheda si e' rifatta un elenco suo"

    m = re.search(r"_group\(id, label, body\)\s*\{(.*?)\n  \}", SETTINGS, re.S)
    assert m, "_gruppo ha cambiato forma"
    assert "${body}</section>" in m.group(1), (
        "il gruppo appende di nuovo qualcosa dopo il corpo: le porte fluttuavano "
        "cosi' fra due schede, al primo tentativo"
    )


def test_i_tetti_si_leggono_e_si_cambiano_altrove() -> None:
    """«Quanto ricorda» è una domanda con una risposta da leggere."""
    body = _body("_renderHowMuchItRemembers")
    assert "_measureCap(" in body
    assert "_numberField(" not in body, "i campi modificabili sono tornati in cassetto"
    assert 'data-summary="tetti"' in body, "manca il modo di cambiarli"
    panel = _body("_openCaps")
    assert panel.count("_numberField(") == 3, "i tre campi non sono nel pannello"


def test_la_misura_dice_quanto_resta_non_solo_quanto_misura() -> None:
    """«2.090 su 3.000» va letto e sottratto; «restano 910» è la risposta.

    E sopra il tetto la frase cambia del tutto, perché cambia la conseguenza:
    Dream smette di scrivere.
    """
    body = _body("_capState")
    assert "_capState(" in _body("_measureCap")
    assert "settings.memory.headroom" in body
    assert "headroomOver" in body, "sopra il tetto non si dice cosa succede"
    import json

    for lingua in ("it", "en"):
        mem = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
        mem = mem["settings"]["memory"]
        assert "{left}" in mem["headroom"] and "{over}" in mem["headroomOver"]


def test_una_marca_e_una_riga_con_chi_risponde() -> None:
    """La pastiglia «risponde» la scheda non ce l'aveva: da qui si amministrano
    le marche, e sapere quale sta rispondendo è il contesto di ogni decisione."""
    body = _body("_renderProviderListHtml")
    assert 'class="brand-row"' in body and "provider-card" not in body
    assert "brand-answers" in body and "settings.answersNow" in body
    assert "provider-edit" not in body and "provider-delete" not in body, (
        "modifica ed elimina sono rimaste nella riga"
    )
    assert "provider-edit" in _body("_openBrand")
    m = re.search(r"^\.brand-row \{(.*?)\}", CSS, re.S | re.M)
    assert m and re.search(r"min-height:\s*52px", m.group(1))


@requires_node
def test_il_colore_della_marca_e_uno_solo_e_mai_grigio() -> None:
    """Officina e casa colorano una marca con la stessa funzione: prima erano
    due regole (una tinta dal nome qui, la tabella in casa), e la stessa marca
    aveva due colori. E una tabella da sola lascerebbe grigie proprio le marche
    che l'utente si è aggiunto da sé: per quelle c'è la tinta dal nome.
    ``getProviderBrand`` vero, importato."""
    assert "getProviderBrand(name).color" in _body("_brandColor")
    home = (ASSETS / "home-model.js").read_text(encoding="utf-8")
    assert "getProviderBrand(p.name).color" in home
    brand = (ASSETS / "shared" / "provider-brand.js").as_uri()
    out = run_js(
        f"const {{ getProviderBrand }} = await import({json.dumps(brand)});\n"
        """
const colori = ['openai', 'anthropic', 'la-mia-marca', 'altra'].map((n) => getProviderBrand(n).color);
console.log(JSON.stringify(colori));
console.log(getProviderBrand('la-mia-marca').color === getProviderBrand('la-mia-marca').color);
"""
    )
    colori, stabile = out.strip().splitlines()
    colori = json.loads(colori)
    assert colori[0] == "#10a37f", "una marca conosciuta ha perso il suo colore"
    assert all(c != "#888" for c in colori), f"una marca aggiunta a mano è grigia: {colori}"
    assert colori[2].startswith("hsl(") and colori[2] != colori[3]
    assert stabile == "true"


def test_tenere_sveglia_la_cpu_e_un_comando_a_segmenti() -> None:
    """Tre voci stanno in riga; il criterio era già scritto nel codice."""
    body = _body("_renderKeepAwake")
    assert "settings-seg" in body and "<select" not in body
    assert "keepAwakeShort" in body, "le parole lunghe non stanno in un terzo di riga"
    assert 'role="radiogroup"' in body and 'role="radio"' in body
    # Il testo lungo — «(consigliato)» compreso — non si perde: va nel title.
    assert "title=" in body and "settings.battery.keepAwake.$" in body.replace("{id}", "$")


def test_il_bottone_principale_e_pieno_e_uno_solo() -> None:
    """Sei bottoni pieni sulla stessa pagina non ne fanno risaltare nessuno."""
    assert SETTINGS.count("settings-btn-full") == 1, (
        "il modificatore è finito su più di un'azione"
    )
    assert "settings.addProviderHint" in SETTINGS, "manca la riga che spiega cosa comporta"
    m = re.search(r"^\.settings-btn-full \{(.*?)\}", CSS, re.S | re.M)
    assert m and "var(--on-accent)" in m.group(1), (
        "testo non su --on-accent: con un accento chiaro non si legge"
    )


# ── Terzo giro: dettagli ─────────────────────────────────────────────────────


def test_la_finestra_di_contesto_ha_finalmente_un_comando() -> None:
    """Esisteva nello schema, nel payload e nella rotta — con due soli valori
    accettati — e **nessuna schermata la mostrava**. Non era un dato mancante:
    era un comando mancante."""
    body = _body("_renderParameters")
    assert "context_window_tokens" in body
    # L'**espressione**, non la parola: `context_window_options` compare anche
    # nel commento sopra, e cercarla lì lasciava passare la mutazione che
    # ricopiava l'elenco a mano (misurato il 21/09/2026, banco verde su codice
    # rotto). E nessun numero scritto qui: la rotta ne rifiuta ogni altro, e
    # due copie divergono in silenzio alla prima aggiunta.
    assert "a.context_window_options" in body, (
        "le voci sono ricopiate qui invece di arrivare dal server"
    )
    codice = "\n".join(
        r for r in SETTINGS.splitlines()
        if not r.lstrip().startswith(("*", "//", "/*"))
    )
    assert not re.search(r"\b(65536|262144)\b", codice), (
        "un valore della finestra di contesto è scritto a mano nel client"
    )
    # E si salva: senza questa chiave il menù cambia e non succede niente.
    assert "'context_window_tokens'" in _body("_wireSections")


def test_le_voci_della_finestra_vengono_dal_server() -> None:
    """La rotta rifiuta qualunque altro valore: due copie dell'elenco
    divergerebbero in silenzio alla prima aggiunta. Stessa forma di
    `power.modes`, che era già così."""
    api = (ROOT / "jenny" / "webui" / "settings_api.py").read_text(encoding="utf-8")
    assert '"context_window_options": list(_CONTEXT_WINDOW_TOKEN_OPTIONS)' in api
    # Tupla e non `set`: la UI ne fa un menù, e l'ordine di un `set` non è
    # garantito fra due esecuzioni.
    assert "_CONTEXT_WINDOW_TOKEN_OPTIONS = (" in api, (
        "l'elenco è tornato un set: l'ordine del menù ballerebbe"
    )


def test_i_numeri_del_menu_hanno_i_separatori() -> None:
    """«65536» non si conta a occhio; «65 536» sì."""
    body = _body("_renderParameters")
    assert "toLocaleString" in body
    sel = _body("_select")
    assert "typeof o === 'object'" in sel, (
        "il menù non sa più separare quel che salva da quel che mostra"
    )


def test_l_intestazione_non_porta_nessuna_pastiglia_di_stato() -> None:
    """L'intestazione di un cassetto diceva anche se la connessione col gateway
    era viva. Non lo dice piu': l'utente l'ha tolta il 21/09/2026.

    Il banco guarda **tutte e quattro** le tracce, perche' reintrodurne una
    sola basta a far tornare la pastiglia a meta': il nodo nel markup, la
    chiave `state` che lo accendeva, il lettore della connessione e il suo
    vestito. Il lettore in particolare non e' un dettaglio: era l'unico motivo
    per cui questo file conosceva `ws-manager`.
    """
    header = (ASSETS / "mobile-header.js").read_text(encoding="utf-8")
    assert "view-title-stato" not in header, "il nodo della pastiglia e' tornato"
    assert "stato: true" not in header, "la chiave che accendeva la pastiglia e' tornata"
    assert "wsManager" not in header, (
        "l'intestazione e' tornata ad ascoltare la connessione: la pastiglia "
        "era il suo unico motivo per conoscerla"
    )
    assert "view-title-stato" not in CSS and "view-title-punto" not in CSS, (
        "il vestito della pastiglia e' rimasto nel CSS"
    )


def test_le_parole_della_pastiglia_non_restano_orfane() -> None:
    """Due stringhe tradotte in due lingue che nessuno legge piu'. Restare non
    e' innocuo: la prossima persona che cerca «connessa» le trova e crede che
    la pastiglia esista ancora da qualche parte."""
    sorgenti = "".join(
        (ASSETS / name).read_text(encoding="utf-8")
        for name in ("mobile-header.js", "mobile-settings.js", "mobile-app.js")
    )
    assert "workshop.state" not in sorgenti
    for lingua in ("it", "en"):
        d = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
        assert "stato" not in d["workshop"], f"{lingua}: le parole della pastiglia sono ancora li'"


def test_i_cassetti_non_hanno_piu_il_bottone_aggiorna() -> None:
    """`activate()` ricarica a ogni apertura e ogni salvataggio ridisegna: un
    bottone che rifà quel che è appena successo insegna a premerlo per
    scaramanzia."""
    header = (ASSETS / "mobile-header.js").read_text(encoding="utf-8")
    m = re.search(r"function drawer\(name\) \{(.*?)\n\}", header, re.S)
    assert m, "drawer() non trovata"
    assert "'refresh'" not in m.group(1), "l'icona «aggiorna» è tornata nei cassetti"


def test_il_cassetto_dice_dove_sta_quel_che_non_ci_sta() -> None:
    """Un cassetto che si chiama «Cervello» sembra il posto dove cercare Dream:
    è l'errore che il giro dei cassetti ha già fatto una volta."""
    assert "workshop.link.dreamInMemory" in SETTINGS
    assert ".settings-link {" in CSS
    import json

    for lingua in ("it", "en"):
        d = json.loads((ASSETS / "i18n" / f"{lingua}.json").read_text(encoding="utf-8"))
        assert d["workshop"]["link"]["dreamInMemory"].strip()


def test_un_lavoro_periodico_e_una_riga() -> None:
    """La quarta voce della tabella «riassumere invece di elencare», che al
    primo giro era rimasta indietro.

    Cinque lavori come schede alte — nome e schedule in testa, «Next:» e
    «Last:» su due righe intere — facevano **oltre un terzo** dei 3 729 px di
    Mani misurati sul telefono il 21/09/2026.
    """
    body = _body("_renderCronJob")
    assert 'class="cron-row' in body, "il lavoro è ancora una scheda"
    assert "cron-card-head" not in body and "cron-lines" not in body
    m = re.search(r"^\.cron-row \{(.*?)\}", CSS, re.S | re.M)
    assert m and re.search(r"min-height:\s*52px", m.group(1))


def test_la_riga_del_lavoro_tiene_quel_che_cambia_il_significato() -> None:
    """Le targhette non sono decorazione: un lavoro **spento** con su scritto
    «fra 4 minuti» sarebbe una bugia. E il pallino dell'esito distingue «ha
    guardato e non c'era niente» da «è andata male»."""
    body = _body("_renderCronJob")
    for pezzo in ("cron.job.disabled", "cron.job.inert", "cron-dot", "couldNotCheck"):
        assert pezzo in body, f"«{pezzo}» è sparito dalla riga"
    # Le due parole spariscono: la colonna di destra *è* il prossimo giro.
    assert "cron.job.next'" not in body and "cron.job.last'" not in body, (
        "«Next:» e «Last:» sono tornate: sono due etichette per due colonne che "
        "si spiegano da sole"
    )


# ── Le skill in Mani ─────────────────────────────────────────────────────────
#
# Dal 21/09/2026 al 24/09/2026 non si vedevano da nessuna parte: la schermata
# Apps che le ospitava era stata cancellata. Tornano in Mani con la forma di
# tutte le altre righe (v. `.agent/officina-skill-plan.md`): in cassetto una
# riga coi due conti, il resto dietro il tocco.


def test_le_skill_in_cassetto_sono_una_riga() -> None:
    body = _body("_renderSkill")
    assert "_summary(" in body, "le skill non sono più riassunte in una riga"
    assert "toggle-switch" not in body, "un interruttore è tornato disteso nel cassetto"


def test_il_riepilogo_delle_skill_non_resta_a_caricamento_per_sempre() -> None:
    body = _body("_loadSkillsSummary")
    assert "catch" in body and "summaryError" in body
    assert "skillsSummary(" in body, "la riga non legge la regola condivisa"


def test_il_pannello_delle_skill_esiste_e_si_disegna_all_apertura() -> None:
    assert 'id="drawer-skill"' in WORKSHOP_HTML
    assert 'id="drawer-skill-body"' in WORKSHOP_HTML
    assert "skill: this._openSkill" in SETTINGS, "niente collega la riga al pannello"
    body = _body("_openSkill")
    # Il pannello vive fuori da `contentEl`: cercarlo lì scrive nel vuoto.
    assert "document.getElementById('drawer-skill-body')" in body
    assert "contentEl" not in body
    assert "splitSkill(" in body, "il pannello divide l'elenco per conto suo"


def test_l_interruttore_passa_dalla_regola_che_sa_chi_sopravvive_al_riavvio() -> None:
    """Le integrate l'avvio le ri-estrae: un interruttore su di loro mente."""
    body = _body("_skillRow")
    assert "controllable(sk)" in body
    assert body.index("controllable(sk)") < body.index("toggle-switch")
    assert "ti-lock" in body, "senza interruttore la riga deve dire perché"


def test_la_scelta_del_21_09_resta_una_scelta() -> None:
    """Crearle, cambiarle e cancellarle non stanno nel pannello: si chiede a
    Jenny. Chi le rimette lo fa sapendolo, non per inerzia."""
    panel = "".join(
        _body(name) for name in ("_openSkill", "_skillRow", "_skillEmpty", "_wireSkill")
    )
    for roba in ("deleteSkill", "/delete", "ti-trash", "ti-edit", "confirmDialog"):
        assert roba not in panel, f"«{roba}» è comparso nel pannello delle skill"


def test_chiedi_a_jenny_scrive_e_non_manda() -> None:
    body = _body("_wireSkill")
    assert "sendInChat" in body and "skills.askPrompt" in body
    assert "sendMessage" not in body
    app = (ASSETS / "mobile-app.js").read_text(encoding="utf-8")
    assert re.search(r"\n  sendInChat\(text\) \{", app), (
        "il guscio non espone più il modo di scrivere nel composer"
    )
